import {readUsage} from './data.js';
import {InvestigationStore, pairedActivity, requestJSON, validateSummary, validateObservation, graphLayout} from './investigation-data.js';

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const raw = value => `<pre class="raw">${esc(JSON.stringify(value, null, 2) ?? '未提供')}</pre>`;
const at = value => Number.isFinite(Date.parse(value)) ? new Date(value).toISOString().replace('T', ' ') : '—';
const number = (value, suffix = '', factor = 1) => typeof value === 'number' && Number.isFinite(value) ? `${(value * factor).toLocaleString('zh-TW', {maximumFractionDigits: 2})}${suffix}` : '—';
const statusText = {running: '調查中', completed: '已完成調查', failed: '調查失敗', interrupted: '調查中斷'};
const outcomeText = {report_ready: '報告已保存', unresolved: '證據不足，未解決', budget_exhausted: '調查額度用盡', execution_failed: '執行失敗', interrupted: '執行中斷'};
const health = {ok: '正常', warning: '警告', failing: '異常', unknown: '無資料'};
const badge = summary => `<span class="badge ${summary.status === 'running' ? 'active' : ['failed', 'interrupted'].includes(summary.status) ? 'failed' : ''}">${esc(statusText[summary.status] || summary.status)}</span>`;
const store = new InvestigationStore();
const errors = new Map(), details = new Map(), contexts = new Map(), eventLoads = new Map(), logs = new Map();
let graph = null, graphReceivedAt = null, selectedNode = null, zoom = 1, fit = true, layoutSignature = '';
let stream = null, logStream = null, streamRetry = null, logRetry = null, heartbeatTimer = null, streamDelay = 1000, logDelay = 1000;
let lastActivity = 0, lastLogActivity = 0, disposed = false, graphGeneration = 0;
let history = [], nextBefore = null, historyBusy = false, historyAgain = false, selectedHistory = null, detailGeneration = 0, filter = 'all';
let journalFilter = 'all', posting = false, pendingRequest = null, refreshBusy = false;
const storageKey = 'nightwatch.investigation.pending.v1';
const localTime = value => Number.isFinite(typeof value === 'number' ? value : Date.parse(value)) ? new Intl.DateTimeFormat('zh-TW', {timeZone: 'Asia/Taipei', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'}).format(new Date(value)) : '—';
const nodeSlots = new Map();
let liveGraph = null, liveReceivedAt = null, viewingHistory = false;
let snapshots = [], timelineBounds = null, cursorTime = null, targetSnapshot = null;
let snapshotBusy = false, snapshotMessage = '', snapshotIndexError = '', snapshotIndexBusy = false;
let snapshotController = null, snapshotIndexController = null, snapshotRequest = 0, snapshotTimer = null, snapshotPoll = null, lastSnapshotRequest = 0;

function updateGraphMarkup(id, markup) {
  const container = $(id), range = document.createRange();
  range.selectNodeContents(container);
  const fragment = range.createContextualFragment(markup);
  const key = node => node.nodeType === 1 ? node.getAttribute('data-node') ?? node.getAttribute('data-snapshot') : null;
  function sync(parent, desired) {
    const keyed = new Map([...parent.childNodes].filter(node => key(node) !== null).map(node => [key(node), node]));
    let cursor = parent.firstChild;
    for (const next of [...desired.childNodes]) {
      const nextKey = key(next);
      let current = nextKey === null ? cursor : keyed.get(nextKey);
      if (!current || key(current) !== nextKey || current.nodeType !== next.nodeType || current.nodeName !== next.nodeName || current.namespaceURI !== next.namespaceURI) {
        current = next.cloneNode(true);
        parent.insertBefore(current, cursor);
      } else {
        if (current !== cursor) parent.insertBefore(current, cursor);
        if (current.nodeType === 1) {
          // Keep a user's expanded node details open across observation updates.
          const keepOpen = current.localName === 'details';
          for (const attribute of [...current.attributes]) {
            if (!(keepOpen && attribute.name === 'open') && !next.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
          }
          for (const attribute of [...next.attributes]) {
            if (!(keepOpen && attribute.name === 'open') && current.getAttribute(attribute.name) !== attribute.value) current.setAttribute(attribute.name, attribute.value);
          }
          sync(current, next);
        } else if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
      }
      cursor = current.nextSibling;
    }
    while (cursor) { const next = cursor.nextSibling; cursor.remove(); cursor = next; }
  }
  sync(container, fragment);
}

function timelineItems() {
  return snapshots.filter(item => !timelineBounds || (item.time >= timelineBounds.from && item.time <= timelineBounds.to));
}
function renderTimeline() {
  updateStartButton();
  const items = timelineItems(), range = $('snapshot-range');
  $('graph-mode').textContent = snapshotBusy ? '歷史快照載入中' : viewingHistory ? '歷史 graph' : '即時 graph';
  $('graph-live').disabled = !viewingHistory;
  $('graph-view-time').textContent = graph ? `${snapshotBusy ? '目前顯示' : viewingHistory ? '歷史快照' : '即時觀測'} ${localTime(graph.at)} (+08:00) · #${graph.seq}` : snapshotBusy ? '正在讀取歷史快照…' : '目前沒有可顯示的快照';
  $('graph-canvas').setAttribute('aria-busy', String(snapshotBusy));
  const index = items.findIndex(item => item.seq === targetSnapshot?.seq);
  $('snapshot-prev').disabled = !items.length || (viewingHistory && index === 0);
  $('snapshot-next').disabled = !viewingHistory || !items.length || index === items.length - 1;
  range.disabled = !items.length;
  if (timelineBounds) {
    // Keep native slider values small enough for accessibility APIs' numeric precision.
    range.min = 0; range.max = Math.max(1, timelineBounds.to - timelineBounds.from);
    range.value = (viewingHistory ? cursorTime ?? timelineBounds.to : timelineBounds.to) - timelineBounds.from;
    $('snapshot-from').textContent = `${localTime(timelineBounds.from)} ${viewingHistory ? '範圍起點' : '最早'}`;
    $('snapshot-to').textContent = `${localTime(timelineBounds.to)} ${viewingHistory ? '範圍終點' : '最新保存'}`;
    const span = timelineBounds.to - timelineBounds.from || 1;
    updateGraphMarkup('snapshot-ticks', items.map(item => `<i data-snapshot="${item.seq}" style="left:${(item.time - timelineBounds.from) / span * 100}%"></i>`).join(''));
  } else {
    $('snapshot-from').textContent = $('snapshot-to').textContent = '—';
    $('snapshot-ticks').textContent = '';
  }
  const label = viewingHistory ? `游標 ${localTime(cursorTime)}` : `${items.length} 張可用快照`;
  $('snapshot-target').textContent = label;
  range.setAttribute('aria-valuetext', `${label}${targetSnapshot ? `，快照 ${localTime(targetSnapshot.at)}` : ''}，台灣時間`);
  $('snapshot-note').textContent = snapshotBusy ? `正在載入 ${localTime(targetSnapshot?.at)}；${graph ? `暫時保留 ${localTime(graph.at)} 的圖，完成後切換。` : '尚無可顯示的圖。'}` : !items.length ? '目前沒有保留快照；即時觀測仍可使用。' : viewingHistory ? `選取不晚於游標的快照；快照間沒有保存的狀態不補算。右側調查與 Monitor 紀錄仍為即時。` : '拖曳可查看歷史；最右端是最新保存快照。時間均為台灣時間 (+08:00)。';
  $('snapshot-error').textContent = [snapshotIndexError, snapshotMessage].filter(Boolean).join(' ');
  $('snapshot-error').hidden = !snapshotIndexError && !snapshotMessage;
}
function cancelSnapshot() {
  clearTimeout(snapshotTimer); snapshotTimer = null;
  snapshotController?.abort(); snapshotController = null; snapshotRequest++;
}
async function loadSnapshotIndex() {
  if (snapshotIndexBusy || disposed) return;
  snapshotIndexBusy = true;
  const controller = new AbortController(); snapshotIndexController = controller;
  try {
    const all = new Map(); let before = null;
    do {
      const page = await requestJSON(`/api/graph/snapshots?limit=500${before === null ? '' : '&before_seq=' + before}`, {signal: controller.signal});
      if (!Array.isArray(page.snapshots) || !(page.next_before_seq === null || Number.isSafeInteger(page.next_before_seq))) throw new Error('快照清單或分頁格式不符。');
      let previous = before ?? Infinity;
      for (const item of page.snapshots) {
        if (!Number.isSafeInteger(item.seq) || item.seq < 0 || typeof item.at !== 'string' || !Number.isFinite(Date.parse(item.at)) || item.seq >= previous) throw new Error('快照索引缺少合法 seq、at 或未依序排列。');
        all.set(item.seq, {...item, time: Date.parse(item.at)}); previous = item.seq;
      }
      if (page.next_before_seq !== null && (!page.snapshots.length || page.next_before_seq !== previous || page.next_before_seq >= (before ?? Infinity))) throw new Error('快照分頁沒有前進。');
      before = page.next_before_seq;
    } while (before !== null && !disposed);
    if (disposed) return;
    const ordered = [...all.values()].sort((a, b) => a.time - b.time || a.seq - b.seq);
    // timestamp resolves equal times to the largest seq; expose only retrievable entries.
    snapshots = [...new Map(ordered.map(item => [item.time, item])).values()];
    snapshotIndexError = '';
    if (!viewingHistory) timelineBounds = snapshots.length ? {from: snapshots[0].time, to: snapshots[snapshots.length - 1].time} : null;
    if (viewingHistory && targetSnapshot && !all.has(targetSnapshot.seq)) {
      cancelSnapshot(); snapshotBusy = false; graph = null;
      snapshotMessage = '此快照已過期或不再可用。請選擇其他時間，或回到即時。';
      targetSnapshot = null; renderGraph();
    }
  } catch (e) {
    if (!controller.signal.aborted) snapshotIndexError = `歷史清單讀取失敗：${e.message}；按「更新資料」可重試。`;
  } finally {
    snapshotIndexBusy = false;
    if (!disposed) renderTimeline();
  }
}
function selectSnapshot(time, immediate = false) {
  if (!Number.isFinite(time) || disposed) return;
  const items = timelineItems();
  const item = [...items].reverse().find(candidate => candidate.time <= time);
  viewingHistory = true; cursorTime = time;
  if (item && targetSnapshot?.seq === item.seq && !snapshotMessage && (snapshotBusy || graph?.seq === item.seq)) {
    if (immediate && snapshotTimer) { clearTimeout(snapshotTimer); snapshotTimer = null; void fetchSnapshot(item, snapshotRequest); }
    renderTimeline(); return;
  }
  cancelSnapshot(); targetSnapshot = item ?? null; snapshotMessage = '';
  snapshotBusy = Boolean(item);
  if (!item) { graph = null; snapshotMessage = '此時間沒有保留快照，請選擇較新的時間。'; }
  renderGraph();
  if (!item) return;
  const request = snapshotRequest;
  const delay = immediate ? 0 : Math.max(0, 150 - (Date.now() - lastSnapshotRequest));
  snapshotTimer = setTimeout(() => { snapshotTimer = null; void fetchSnapshot(item, request); }, delay);
}
async function fetchSnapshot(item, request) {
  lastSnapshotRequest = Date.now();
  const controller = new AbortController(); snapshotController = controller;
  try {
    const params = new URLSearchParams({timestamp: item.at});
    const value = await requestJSON(`/api/graph?${params}`, {signal: controller.signal});
    validateObservation(value);
    if (disposed || request !== snapshotRequest || !viewingHistory) return;
    // A pruned snapshot may resolve to a different, earlier graph. Never relabel it.
    if (Date.parse(value.at) !== item.time || value.seq !== item.seq) throw new Error('所選快照已改變或過期，請重新選擇時間。');
    graph = value; graphReceivedAt = null; snapshotMessage = '';
  } catch (e) {
    if (disposed || controller.signal.aborted || request !== snapshotRequest) return;
    graph = null;
    snapshotMessage = e.status === 404 ? '此快照已過期或沒有可用資料。請選擇其他時間。' : `歷史快照讀取失敗：${e.message}`;
    void loadSnapshotIndex();
  } finally {
    if (!disposed && request === snapshotRequest) { snapshotBusy = false; snapshotController = null; renderGraph(); }
  }
}
function stepSnapshot(direction) {
  const items = timelineItems();
  if (!items.length) return;
  const index = items.findIndex(item => item.seq === targetSnapshot?.seq);
  const item = !viewingHistory ? items[items.length - 1] : index < 0 ? (direction < 0 ? [...items].reverse().find(item => item.time < cursorTime) : items.find(item => item.time > cursorTime)) : items[index + direction];
  if (item) selectSnapshot(item.time, true);
}
function returnToLive() {
  cancelSnapshot(); viewingHistory = false; snapshotBusy = false; snapshotMessage = ''; targetSnapshot = null; cursorTime = null;
  graph = liveGraph; graphReceivedAt = liveReceivedAt;
  timelineBounds = snapshots.length ? {from: snapshots[0].time, to: snapshots[snapshots.length - 1].time} : null;
  renderGraph(); void loadSnapshotIndex(); void refresh();
}

function error(key, message) {
  if (message) errors.set(key, message); else errors.delete(key);
  $('errors').innerHTML = [...errors.values()].map(message => `<div class="error">${esc(message)}</div>`).join('');
}
function connection(text, kind = '') { $('connection').textContent = text; $('connection').className = kind; }
function savePending(value) {
  pendingRequest = value;
  try { if (value) sessionStorage.setItem(storageKey, JSON.stringify(value)); else sessionStorage.removeItem(storageKey); }
  catch { error('storage', '瀏覽器無法保存待確認的 request_id；請勿在提交結果未明時重新整理。'); }
}
function activeId() { return store.state?.active_investigation_id ?? null; }
function activeSummary() { return store.state?.active_investigation ?? null; }
function updateStartButton() {
  $('start-investigation').disabled = posting || !store.state || Boolean(activeId());
  $('start-investigation').textContent = posting ? '正在送出…' : pendingRequest ? '重試同一次調查' : viewingHistory ? '調查即時狀態' : '開始調查';
}
function acceptGraph(value, receivedAt = null) {
  validateObservation(value);
  // Avoid a slow state GET rolling an independently delivered graph backwards.
  if (liveGraph && Date.parse(value.at) < Date.parse(liveGraph.at)) return;
  if (liveGraph && value.at === liveGraph.at && value.seq < liveGraph.seq) return;
  liveGraph = value; liveReceivedAt = receivedAt; graphGeneration++;
  if (!viewingHistory) { graph = value; graphReceivedAt = receivedAt; renderGraph(); }
}
function acceptState(value, options = {}) {
  const previous = activeId();
  if (!store.acceptState(value, options)) return;
  if (options.acceptGraph !== false && value.graph !== null) acceptGraph(value.graph, value.graph_received_at);
  error('graph', value.graph_error ? `服務拓樸來源：${value.graph_error}。保留最後取得的快照。` : value.graph === null ? '目前沒有可用的服務拓樸。調查歷史仍可讀取。' : null);
  error('state');
  updateStartButton(); renderCurrent();
  if (activeId()) {
    if (previous !== activeId() || !store.events.has(activeId())) void loadEvents(activeId());
    if (previous !== activeId() || !details.has(activeId())) void loadDetail(activeId(), false);
  }
  if (previous !== activeId()) void loadHistory();
  renderUsagePage();
}

function renderGraph() {
  renderTimeline();
  $('graph-canvas').hidden = !graph;
  $('active-phase').textContent = activeId() ? '調查中' : store.state ? '無進行中調查' : '等待狀態';
  if (!graph) {
    $('graph-empty').hidden = false; $('graph-empty').textContent = snapshotBusy ? '正在讀取歷史快照…' : '尚無可顯示的服務拓樸，請查看快照與連線訊息。';
    $('nodes').textContent = ''; $('edges').innerHTML = ''; $('sources').textContent = '';
    for (const id of ['total-nodes', 'failing-count', 'warning-count', 'snapshot-at']) $(id).textContent = '—';
    $('graph-count').textContent = snapshotBusy ? '載入中' : '沒有快照'; $('match-count').textContent = '';
    $('footer-status').textContent = viewingHistory ? '歷史模式 · 尚無可顯示的快照' : '等待即時快照';
    renderNode();
    return;
  }
  if (!graph.nodes.some(n => n.id === selectedNode)) selectedNode = graph.nodes[0]?.id ?? null;
  for (const node of graphLayout(graph)) if (!nodeSlots.has(node.id)) nodeSlots.set(node.id, nodeSlots.size);
  const layout = graph.nodes.map(node => { const slot = nodeSlots.get(node.id); return {id: node.id, layout: {row: Math.floor(slot / 4), col: slot % 4}}; });
  const signature = String(nodeSlots.size);
  if (signature !== layoutSignature) { layoutSignature = signature; fit = true; }
  const width = Math.min(4, Math.max(1, nodeSlots.size)) * 160 + 24;
  const height = Math.max(1, Math.ceil(nodeSlots.size / 4)) * 150 + 24;
  if (fit) zoom = Math.max(.5, Math.min(1.15, ($('graph-viewport').clientWidth - 16) / width));
  const positions = new Map(layout.map(n => [n.id, {x: 20 + n.layout.col * 160, y: 20 + n.layout.row * 150}]));
  const search = $('node-search').value.toLowerCase().trim(), selectedHealth = $('health-filter').value;
  const matches = new Set(graph.nodes.filter(n => (!search || n.id.toLowerCase().includes(search)) && (selectedHealth === 'all' || n.status === selectedHealth)).map(n => n.id));
  $('total-nodes').textContent = graph.nodes.length;
  $('failing-count').textContent = graph.nodes.filter(n => n.status === 'failing').length;
  $('warning-count').textContent = graph.nodes.filter(n => n.status === 'warning').length;
  $('snapshot-at').textContent = localTime(graph.at);
  $('graph-count').textContent = `${graph.nodes.length} 個節點 / ${graph.edges.length} 條連線`;
  $('match-count').textContent = `${matches.size} / ${graph.nodes.length} 個節點符合條件`;
  $('graph-empty').hidden = graph.nodes.length > 0;
  $('graph-empty').textContent = '後端快照目前沒有服務節點。';
  $('graph-canvas').style.width = `${width * zoom}px`; $('graph-canvas').style.height = `${height * zoom}px`;
  for (const id of ['nodes', 'edges']) { $(id).style.width = `${width}px`; $(id).style.height = `${height}px`; $(id).style.transform = `scale(${zoom})`; }
  $('edges').setAttribute('width', width); $('edges').setAttribute('height', height);
  $('zoom-value').textContent = `${Math.round(zoom * 100)}%`;
  updateGraphMarkup('edges', '<defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0L7 3.5L0 7" fill="none" stroke="#72856c"/></marker></defs>' + graph.edges.map((e, index) => {
    const a = positions.get(e.from), b = positions.get(e.to);
    const lane = a.y === b.y ? a.y + 122 + index % 3 * 5 : Math.min(a.y, b.y) + 125 + index % 3 * 5;
    const startY = b.y < a.y ? a.y : a.y + 106;
    const endY = b.y <= a.y ? b.y + 106 : b.y;
    const d = a.y === b.y && Math.abs(a.x - b.x) === 160 ? `M${a.x + (a.x < b.x ? 120 : 0)} ${a.y + 53}H${b.x + (a.x < b.x ? 0 : 120)}` : `M${a.x + 60} ${startY}V${lane}H${b.x + 60}V${endY}`;
    return `<path class="edge ${e.observed ? '' : 'unobserved'} ${e.from === selectedNode || e.to === selectedNode ? 'related' : ''}" d="${d}" marker-end="url(#arrow)"><title>${esc(e.from)} → ${esc(e.to)} · ${esc(e.kind)} · ${e.observed ? '已觀測' : '近期未觀測'}</title></path>`;
  }).join(''));
  updateGraphMarkup('nodes', graph.nodes.map(n => {
    const p = positions.get(n.id);
    const readings = {traffic: n.traffic, errors: n.errors, latency: n.p95_ms, saturation: n.saturation};
    const labels = {traffic: '流量', errors: '錯誤率', latency: 'P95', saturation: '飽和度', liveness: '存活訊號'};
    const preferred = n.kind === 'datastore' ? ['latency', 'traffic', 'errors', 'saturation'] : ['queue', 'volume'].includes(n.kind) ? ['saturation', 'traffic', 'latency', 'errors'] : ['traffic', 'latency', 'errors', 'saturation'];
    const axis = n.primary_axis ?? preferred.find(key => Number.isFinite(readings[key]));
    const values = {traffic: number(n.traffic, ' req/s'), errors: number(n.errors, '%', 100), latency: number(n.p95_ms, ' ms'), saturation: number(n.saturation, '%', 100), liveness: n.alive === true ? 'true' : n.alive === false ? 'false' : '—'};
    const primary = axis ? `${labels[axis]} ${values[axis] === '—' ? '未回報' : values[axis]}` : '尚無量測';
    return `<button class="graph-node ${esc(n.kind)} ${selectedNode === n.id ? 'selected' : ''} ${matches.has(n.id) ? '' : 'dim'}" data-node="${esc(n.id)}" style="left:${p.x}px;top:${p.y}px" aria-pressed="${selectedNode === n.id}"><span class="node-name">${esc(n.id)}</span><span class="node-meta ${esc(n.status)}"><span><i class="dot ${esc(n.status)}"></i>${esc(health[n.status])}</span><span>觀測</span></span><span class="node-value">${esc(primary)}</span></button>`;
  }).join(''));
  $('nodes').querySelectorAll('[data-node]').forEach(button => button.onclick = () => locateNode(button.dataset.node));
  updateGraphMarkup('sources', '<span>觀測來源</span>' + ['prometheus', 'jaeger', 'logstore'].map(key => {
    const s = graph.sources[key];
    return `<span>${key} · ${s?.ok === true ? '可用' : s?.ok === false ? '不可用' : '無資料'} · ${esc(number(s?.age_secs, ' 秒前'))}</span>`;
  }).join(''));
  $('footer-status').textContent = `${snapshotBusy ? '目前顯示' : viewingHistory ? '歷史' : '即時'}快照 #${graph.seq} · 觀測時間 ${localTime(graph.at)} (+08:00)${viewingHistory ? '；右側調查與 Monitor 紀錄仍為即時' : ` · 後端接收 ${at(graphReceivedAt)}；心跳不代表新量測`}`;
  renderNode();
}
function locateNode(id) {
  if (!graph?.nodes.some(n => n.id === id)) { error('locate', `圖外來源：${id}，目前拓樸無法定位。`); return; }
  error('locate'); selectedNode = id; $('node-search').value = ''; $('health-filter').value = 'all';
  renderGraph();
  for (const node of $('nodes').querySelectorAll('[data-node]')) if (node.dataset.node === id) node.scrollIntoView({block: 'nearest', inline: 'center'});
}
function renderNode() {
  const n = graph?.nodes.find(n => n.id === selectedNode);
  if (!n) { updateGraphMarkup('node-detail', '<div class="empty">點選節點查看後端量測。</div>'); return; }
  const metrics = [['請求量', number(n.traffic, ' req/s')], ['錯誤率', number(n.errors, '%', 100)], ['P95', number(n.p95_ms, ' ms')], ['飽和度', number(n.saturation, '%', 100)], ['alive', n.alive === true ? 'true' : n.alive === false ? 'false' : '—']];
  updateGraphMarkup('node-detail', `<div class="node-detail-top"><h3>${esc(n.id)}</h3><span class="${esc(n.status)}">${esc(health[n.status])}</span></div><div class="node-metrics">${metrics.map(([label, value]) => `<div><span class="metric-label">${label}</span><strong>${esc(value)}</strong></div>`).join('')}</div><p class="report-note">alive 與來源量測的定義由後端決定；調查結束不會改變服務健康。</p><details><summary>原始節點與相鄰連線</summary>${raw({node: n, edges: graph.edges.filter(e => e.from === n.id || e.to === n.id)})}</details>`);
}

function activityHTML(id, terminal = false, sourceFilter = 'all') {
  return pairedActivity(store.activity(id), terminal).filter(row => sourceFilter === 'all' || (row.kind === 'tool' ? sourceFilter === 'agent' : sourceFilter === 'system')).map(row => {
    if (row.kind === 'event') {
      const e = row.event;
      return `<article class="journal-entry system"><div class="journal-meta"><strong>系統</strong><span>${esc(e.type)}</span><time>${esc(at(e.at))}</time></div>${raw(e.payload)}</article>`;
    }
    const end = row.finished, start = row.started;
    const label = {running: '執行中', recorded: '已取得證據', reported: '已提交報告', failed: '工具失敗', incomplete: '調查已結束，未收到工具結果'}[row.status];
    return `<article class="journal-entry agent"><div class="journal-meta"><strong>Agent 工具</strong><span>${esc(start?.payload.tool ?? end?.payload.tool ?? '未提供工具名稱')}</span></div><p class="${row.status === 'failed' || row.status === 'incomplete' ? 'failing' : ''}">${label}</p><p class="report-note">${esc(row.investigation_id)} / ${esc(row.call_id)}${start ? '' : ' · 未收到開始事件'}</p>${start ? `<details><summary>開始時間、參數與內容</summary><p>${esc(at(start.at))}</p>${raw(start.payload)}</details>` : ''}${end ? `<details open><summary>${row.status === 'failed' ? '失敗原因' : row.status === 'reported' ? '提交的報告' : '證據 payload.evidence'}</summary>${row.status === 'recorded' && end.payload.evidence === undefined ? '<p class="failing">成功事件未提供 evidence。</p>' : raw(row.status === 'recorded' ? end.payload.evidence : row.status === 'reported' ? end.payload.report : end.payload)}</details>` : ''}</article>`;
  }).join('') || '<div class="empty">尚未收到此調查的事件。</div>';
}
function renderUsage(usage) {
  const u = readUsage(usage);
  $('agent-usage').innerHTML = `<div class="usage-heading"><h3>Agent 用量</h3><span>${usage == null ? '尚未回報' : '後端累計'}</span></div><div class="usage-primary"><div><span class="metric-label">總 token</span><strong>${number(u.total)}</strong></div><div><span class="metric-label">快取命中率</span><strong>${number(u.rate, '%', 100)}</strong></div></div>${u.problems.length ? `<p class="failing">${esc(u.problems.join(' '))}</p>` : ''}<details><summary>後端用量原始值</summary>${raw(usage)}</details>`;
}
function usageId() { return activeId() || store.state?.last_completed_investigation_id; }
function renderUsagePage() {
  const view = $('usage-view');
  if (view.hidden) return;
  const id = usageId(), detail = details.get(id), usage = detail?.usage;
  const cached = usage?.cache_read_tokens !== undefined ? usage.cache_read_tokens : usage?.cached_tokens;
  const calls = usage?.requests !== undefined ? usage.requests : usage?.calls;
  const u = readUsage(usage && typeof usage === 'object' && !Array.isArray(usage) ? {...usage, cached_tokens: cached, calls} : usage);
  const problems = u.problems.map(message => message.replaceAll('cached_tokens', usage?.cache_read_tokens !== undefined ? 'cache_read_tokens' : 'cached_tokens').replaceAll('calls', usage?.requests !== undefined ? 'requests' : 'calls'));
  const extra = key => {
    const value = usage?.[key];
    if (value === undefined) return null;
    if (!Number.isSafeInteger(value) || value < 0) { problems.push(`${key} 必須是非負安全整數。`); return null; }
    return value;
  };
  const writes = extra('cache_write_tokens'), tools = extra('tool_calls');
  const reported = [u.input_tokens, u.output_tokens, u.cached_tokens, u.calls, writes, tools].some(value => value !== null);
  const stateLabel = !store.state ? '等待調查狀態' : !id ? '尚無調查' : !detail ? '尚未取得用量' : problems.length ? '資料異常' : reported ? '已回報' : '尚未回報';
  const expanded = view.querySelector('details')?.open;
  const trend = (title, unit) => `<article class="usage-view-chart"><div class="usage-view-chart-heading"><h2>${title}</h2><span>${unit}</span></div><div class="usage-view-chart-empty"><svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.25" aria-hidden="true"><path d="M4 4v16h16"/><circle cx="14" cy="10" r="5"/><path d="M14 7v3l2 1"/></svg><strong>尚無趨勢資料</strong><p>目前只有調查累計值，尚無各時間點的用量。</p></div></article>`;
  view.innerHTML = `<div class="usage-view-context"><div><span class="usage-view-kicker">${activeId() ? '進行中的調查' : '最近一次調查'}</span><p>${id ? esc(id) : '開始調查後，這裡會顯示 Agent 的用量。'}</p></div><div class="usage-view-context-actions"><span class="usage-view-status ${problems.length ? 'usage-view-invalid' : ''}">${stateLabel}</span>${id ? `<a href="#investigations/${encodeURIComponent(id)}">查看調查 →</a>` : ''}<a href="#topology">返回服務拓樸 →</a></div></div>
    <div class="usage-view-metrics">
      <article><h2>總 token</h2><strong>${number(u.total)}</strong><p>輸入 + 輸出</p></article>
      <article><h2>Prompt cache 命中率</h2><strong>${number(u.rate, '%', 100)}</strong><p>${u.input_tokens === 0 ? '尚無輸入，命中率不適用' : '快取讀取 / 全部輸入'}</p><div class="usage-view-meter" ${u.rate === null ? 'aria-hidden="true"' : `role="meter" aria-label="Prompt cache 命中率" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${u.rate * 100}"`}><span style="width:${u.rate === null ? 0 : u.rate * 100}%"></span></div></article>
      <article><h2>模型請求</h2><strong>${number(u.calls)} <small>次</small></strong><p>本次調查的模型請求數</p></article>
      <article><h2>工具呼叫</h2><strong>${number(tools)} <small>次</small></strong><p>本次調查的工具呼叫數</p></article>
    </div>
    ${problems.length ? `<p class="usage-view-error" role="alert">${esc(problems.join(' '))}</p>` : !reported ? '<p class="usage-view-note">尚未取得用量數字；「—」不代表 0。若讀取失敗，請查看上方錯誤並按「更新資料」。</p>' : ''}
    <div class="usage-view-section-title"><h2>用量趨勢</h2><span>本次調查</span></div><div class="usage-view-charts">${trend('Token 使用量', 'tokens')}${trend('Prompt cache 命中率', '%')}</div>
    <section class="usage-view-breakdown"><div class="usage-view-section-title"><h2>Token 明細</h2><span>後端累計</span></div><dl><div><dt>輸入 token</dt><dd>${number(u.input_tokens)}</dd></div><div><dt>輸出 token</dt><dd>${number(u.output_tokens)}</dd></div><div><dt>其中快取讀取</dt><dd>${number(u.cached_tokens)}</dd></div><div><dt>快取寫入</dt><dd>${number(writes)}</dd></div></dl><p class="usage-view-note">快取讀取包含於輸入，總量不重複加總。命中率代表輸入 token 的快取比例，與回答正確率無關。</p></section>
    <details class="usage-view-raw" ${expanded ? 'open' : ''}><summary>統計口徑與原始值</summary><p>範圍為上方這次調查，不代表帳號總用量。總 token = input_tokens + output_tokens。Prompt cache 命中率 = 快取讀取 ÷ input_tokens × 100%。未回報欄位顯示「—」，實際回報 0 才顯示 0。</p><p>數字以最近一次取得的調查詳情為準，可按「更新資料」重新讀取。</p>${raw(usage)}</details>`;
}
let usageLoadingId = null;
async function loadUsagePage() {
  if ($('usage-view').hidden) return;
  renderUsagePage();
  const id = usageId();
  if (!id || id === usageLoadingId) return;
  usageLoadingId = id;
  try { await loadDetail(id, false); }
  finally { if (usageLoadingId === id) usageLoadingId = null; }
}
function reportHTML(detail) {
  if (!detail) return '<p class="report-note">正在讀取調查詳情。</p>';
  const report = detail.report;
  const agentReport = report?.investigation_report ?? report?.agent_report;
  if (!report) return `<p class="${detail.status === 'running' ? 'report-note' : 'failing'}">${detail.status === 'running' ? '調查尚未結束，報告尚未產生。' : '調查已結束，但後端未提供報告。'}</p>`;
  return `<p class="detail-summary">${esc(report.summary_zh || '報告未提供摘要。')}</p><p>${esc(outcomeText[report.outcome] || report.outcome || '未提供結果')}</p><p class="report-note">${esc(at(report.started_at))} → ${esc(at(report.closed_at))}。調查完成不表示服務已恢復。</p><h3>限制</h3>${Array.isArray(report.limitations) && report.limitations.length ? `<ul>${report.limitations.map(item => `<li>${esc(item)}</li>`).join('')}</ul>` : '<p class="report-note">後端未列出限制。</p>'}<h3>Agent 報告</h3>${agentReport == null ? '<p class="report-note">未產生可採信的根因報告；此調查仍有保存結案摘要。</p>' : raw(agentReport)}<details><summary>證據與引用 ID</summary>${raw({evidence_ids: report.evidence_ids, evidence: detail.evidence})}</details>`;
}
function renderCurrent() {
  const id = activeId(), summary = activeSummary(), detail = id ? details.get(id) : null;
  $('active-phase').textContent = id ? '調查中' : store.state ? '無進行中調查' : '等待狀態';
  $('investigation-phase').innerHTML = summary ? badge(summary) : '';
  $('investigation-summary').innerHTML = summary ? `<h3>${esc(summary.summary_zh || '正在調查目前服務狀態')}</h3><p class="report-note">${esc(id)}</p><p>目前可用工具：get_graph</p>` : '<h3>目前沒有進行中的調查</h3><p class="report-note">可開始新調查，或到歷史查看已保存的報告。服務拓樸持續更新。</p>';
  if (!store.state) $('investigation-summary').innerHTML = '<h3>尚未取得調查狀態</h3><p class="report-note">連線恢復後才能確認目前是否有調查。</p>';
  renderUsage(detail?.usage);
  const agent = id && journalFilter !== 'monitor' ? activityHTML(id, false, journalFilter) : '';
  const monitor = journalFilter === 'all' || journalFilter === 'monitor' ? [...logs.values()].map(log => `<article class="journal-entry monitor"><div class="journal-meta"><strong>Monitor</strong><span>${esc(log.monitor_id)} · ${esc(log.level)}</span><time>${esc(at(log.occurred_at))}</time></div><p>${esc(log.message)}</p><div class="journal-links">${log.refs.node_ids.map(node => `<button class="node-link" data-locate-node="${esc(node)}">${esc(node)} ↗</button>`).join('')}</div><details><summary>原始 log</summary>${raw(log)}</details></article>`).join('') : '';
  $('journal-entries').innerHTML = agent + monitor || '<div class="empty">目前沒有符合來源的紀錄。</div>';
  $('journal-count').textContent = `${id ? store.activity(id).length : 0} 個調查事件 / ${logs.size} 筆 Monitor log`;
  $('journal-note').textContent = '調查事件依序配對工具；Monitor 為獨立即時來源，不保證刷新或斷線補送。';
  $('journal-entries').querySelectorAll('[data-locate-node]').forEach(button => button.onclick = () => locateNode(button.dataset.locateNode));
  $('investigation-report').innerHTML = id ? reportHTML(detail) : '<p class="report-note">已結束的調查請至歷史查看保存的報告。</p>';
  const latest = store.state?.last_completed_investigation_id;
  $('current-incident').innerHTML = `<div><h3>${id ? '調查由後端執行，關閉頁面不會取消' : '目前沒有進行中的調查'}</h3><p>調查與服務健康分開顯示。</p></div><a href="#investigations${latest ? '/' + encodeURIComponent(latest) : ''}">${latest ? '查看最近一次報告' : '查看調查歷史'} →</a>`;
  if (!store.state) $('current-incident').innerHTML = '<div><h3>尚未取得調查狀態</h3><p>請查看上方連線錯誤，恢復後按「更新資料」。</p></div>';
}

async function loadEvents(id) {
  if (eventLoads.has(id)) return eventLoads.get(id);
  const task = (async () => {
    let after = 0;
    try {
      do {
        const page = await requestJSON(`/api/investigations/${encodeURIComponent(id)}/events?after=${after}&limit=100`);
        if (!Array.isArray(page.items)) throw new Error('調查事件清單缺少 items。');
        for (const event of page.items) {
          if (event.investigation_id !== id) throw new Error('事件屬於另一個調查。');
          store.acceptEvent(event); // REST pagination never advances stream cursor.
        }
        if (page.next_after === null) break;
        if (!Number.isSafeInteger(page.next_after) || page.next_after <= after) throw new Error('事件分頁 next_after 沒有前進。');
        after = page.next_after;
      } while (!disposed);
      error(`events:${id}`); renderCurrent(); renderDetail();
    } catch (e) { error(`events:${id}`, `調查 ${id} 事件讀取失敗：${e.message}`); }
  })();
  eventLoads.set(id, task);
  try { await task; } finally { eventLoads.delete(id); }
}
async function loadDetail(id, historical = true) {
  try {
    const detail = await requestJSON(`/api/investigations/${encodeURIComponent(id)}`);
    validateSummary(detail);
    if (detail.id !== id) throw new Error('回應的調查 ID 不一致。');
    const previous = details.get(id);
    if (!previous || detail.event_seq >= previous.event_seq) details.set(id, detail);
    error(`detail:${id}`); renderCurrent();
    renderUsagePage();
    if (historical || id === selectedHistory) renderDetail();
  } catch (e) { error(`detail:${id}`, `調查 ${id} 詳情讀取失敗：${e.message}`); renderDetail(); }
}
async function loadHistory(append = false) {
  if (historyBusy) { if (!append) historyAgain = true; return; }
  historyBusy = true; $('more-investigations').disabled = true;
  try {
    const page = await requestJSON(`/api/investigations?limit=20${append && nextBefore !== null ? '&before=' + nextBefore : ''}`);
    if (!Array.isArray(page.items) || !(page.next_before === null || Number.isSafeInteger(page.next_before))) throw new Error('調查歷史分頁格式不符。');
    page.items.forEach(validateSummary);
    const merged = new Map((append ? history : []).map(item => [item.id, item]));
    for (const item of page.items) merged.set(item.id, item);
    history = [...merged.values()].sort((a, b) => b.created_seq - a.created_seq);
    nextBefore = page.next_before; error('history'); renderHistory();
  } catch (e) { error('history', `調查歷史讀取失敗：${e.message}`); renderHistory(); }
  finally {
    historyBusy = false; $('more-investigations').disabled = false;
    if (historyAgain && !disposed) { historyAgain = false; void loadHistory(); }
  }
}
function renderHistory() {
  const query = $('incident-search').value.toLowerCase().trim();
  const rows = history.filter(item => (filter === 'all' || (filter === 'active' ? item.status === 'running' : item.status !== 'running')) && [item.id, item.summary_zh, item.status, item.outcome, statusText[item.status], outcomeText[item.outcome]].some(value => String(value ?? '').toLowerCase().includes(query)));
  $('nav-count').textContent = history.length;
  $('incident-rows').innerHTML = rows.map(item => `<tr><td>${esc(item.id)}</td><td>${badge(item)}</td><td>${esc(item.trigger?.reason || '—')}</td><td>${esc(at(item.started_at))}</td><td>${esc(outcomeText[item.outcome] || item.outcome || '—')}</td><td><a href="#investigations/${encodeURIComponent(item.id)}">查看報告 ↗</a></td></tr>`).join('');
  $('list-empty').hidden = rows.length > 0;
  $('list-empty').textContent = errors.has('history') ? '無法取得調查歷史，請查看上方錯誤。' : '目前沒有符合條件的調查。';
  $('list-count').textContent = `顯示 ${rows.length} / 已載入 ${history.length} 件調查；包含失敗、中斷與未解決。`;
  $('more-investigations').hidden = nextBefore === null;
}
function renderDetail() {
  $('incident-detail').hidden = !selectedHistory;
  if (!selectedHistory) return;
  const detail = details.get(selectedHistory);
  if (!detail) { $('incident-detail').innerHTML = '<div class="empty">調查詳情尚未取得；若失敗請按「更新資料」。</div>'; return; }
  $('incident-detail').innerHTML = `<div class="panel-header"><div><h2>調查報告</h2><span>${esc(detail.id)}</span></div>${badge(detail)}</div><div class="detail-body">${reportHTML(detail)}<details><summary>用量</summary>${raw(detail.usage)}</details><h3>工具與調查紀錄</h3>${activityHTML(detail.id, detail.status !== 'running')}<h3>保存的上下文</h3><p class="report-note">${detail.context_available ? detail.context_complete ? '後端標記上下文完整。' : '上下文不完整；不是完整模型對話。' : '後端尚未保存上下文。'}</p><button id="load-context" ${detail.context_available ? '' : 'disabled'}>讀取保存的上下文</button><div id="saved-context"></div></div>`;
  $('load-context').onclick = () => loadContext(detail.id);
  if (contexts.has(detail.id)) renderContext(contexts.get(detail.id));
}
function renderContext(value) {
  $('saved-context').innerHTML = `<p class="${value.complete ? 'report-note' : 'failing'}">${value.complete ? '後端標記完整的保存上下文。' : '部分上下文（complete: false），不能視為完整模型對話。'}</p>${raw(value.context)}`;
}
async function loadContext(id) {
  const generation = detailGeneration;
  $('load-context').disabled = true;
  try {
    const value = await requestJSON(`/api/investigations/${encodeURIComponent(id)}/context`);
    if (typeof value.complete !== 'boolean' || !value.context || typeof value.context !== 'object') throw new Error('上下文格式不符。');
    if (!contexts.get(id)?.complete || value.complete) contexts.set(id, value);
    if (selectedHistory !== id || generation !== detailGeneration) return;
    renderContext(contexts.get(id));
    error(`context:${id}`);
  } catch (e) { error(`context:${id}`, `上下文讀取失敗：${e.message}`); }
  finally { if (selectedHistory === id && generation === detailGeneration && $('load-context')) $('load-context').disabled = false; }
}
function route() {
  const parts = location.hash.slice(1).split('/');
  const list = parts[0] === 'investigations' || parts[0] === 'incidents';
  const usage = parts[0] === 'usage';
  $('topology-view').hidden = list || usage; $('incidents-view').hidden = !list;
  $('usage-view').hidden = !usage;
  document.querySelector('.investigation-actions').hidden = usage;
  $('nav-usage').setAttribute('aria-current', usage ? 'page' : 'false');
  $('nav-topology').setAttribute('aria-current', list || usage ? 'false' : 'page');
  $('nav-incidents').setAttribute('aria-current', list ? 'page' : 'false');
  $('page-title').textContent = usage ? 'Agent Usage' : list ? '調查歷史與報告' : '調查工作台';
  $('page-description').textContent = usage ? '查看調查的 token 用量、Prompt cache 命中率與趨勢。' : list ? '查看每次調查的結論、工具證據與保存上下文。' : '追蹤服務觀測，並查看目前調查與 Monitor 紀錄。';
  let id = null;
  try { id = list && parts[1] ? decodeURIComponent(parts[1]) : null; error('route'); }
  catch { error('route', '調查網址編碼不合法。'); }
  if (id !== selectedHistory) {
    selectedHistory = id; detailGeneration++; renderDetail();
    if (id) { void loadDetail(id); void loadEvents(id); }
  }
  renderHistory(); if (!list) renderGraph();
  if (usage) void loadUsagePage();
}

async function refresh() {
  if (refreshBusy) return;
  void loadSnapshotIndex();
  refreshBusy = true; $('refresh').disabled = true;
  const generation = graphGeneration;
  try {
    const value = await requestJSON('/api/investigations/state');
    acceptState(value, {acceptGraph: generation === graphGeneration});
  } catch (e) { error('state', `調查狀態讀取失敗：${e.message}`); }
  finally { refreshBusy = false; $('refresh').disabled = false; }
  await loadHistory();
  await loadUsagePage();
  if (selectedHistory) { void loadDetail(selectedHistory); void loadEvents(selectedHistory); }
}
async function startInvestigation() {
  if (posting || !store.state || activeId()) return;
  if (!pendingRequest) savePending({request_id: crypto.randomUUID(), trigger: {source: 'manual', reason: '調查目前服務異常'}});
  posting = true; updateStartButton(); error('create');
  try {
    const result = await requestJSON('/api/investigations', {method: 'POST', body: JSON.stringify(pendingRequest)});
    if (typeof result.investigation_id !== 'string' || !result.investigation_id) throw new Error('建立回應未提供 investigation_id，請以相同 request_id 重試。');
    savePending(null);
    $('submission-status').textContent = `後端已接受 ${result.investigation_id}；目前調查以 state 同步。`;
    void loadDetail(result.investigation_id, false); void loadEvents(result.investigation_id);
    await refresh();
  } catch (e) {
    error('create', `${e.message}${e.details ? ' · ' + JSON.stringify(e.details) : ''}`);
    if (e.status === 409) {
      savePending(null);
      $('submission-status').textContent = '後端拒絕本次操作；沒有自動重送。';
      await refresh();
    } else {
      $('submission-status').textContent = `提交結果未確認。重試會沿用 request_id ${pendingRequest.request_id}，不另開調查。`;
    }
  } finally { posting = false; updateStartButton(); }
}
function reconnect() {
  stream?.close(); stream = null;
  if (disposed || document.hidden || streamRetry) return;
  connection('調查連線中斷，正在重連', 'disconnected');
  streamRetry = setTimeout(() => { streamRetry = null; connect(); }, streamDelay);
  streamDelay = Math.min(8000, streamDelay * 2);
}
function connect() {
  if (disposed || document.hidden || stream) return;
  lastActivity = Date.now(); // Give each connection attempt its own timeout window.
  const requestedCursor = store.eventCursor;
  const events = new EventSource('/api/investigations/stream' + (requestedCursor === null ? '' : `?after=${requestedCursor}`));
  stream = events; let initial = true;
  const receive = handler => event => {
    if (stream !== events || disposed) return;
    try {
      const data = JSON.parse(event.data); handler(data);
      lastActivity = Date.now(); streamDelay = 1000; connection('調查即時連線'); error('stream');
    } catch (e) { error('stream', `調查 SSE 資料錯誤：${e.message}`); reconnect(); }
  };
  events.addEventListener('state', receive(value => {
    acceptState(value, {initialStream: initial && requestedCursor === null}); initial = false;
  }));
  events.addEventListener('graph', receive(value => acceptGraph(value)));
  events.addEventListener('investigation', receive(value => {
    const fresh = store.acceptEvent(value, {stream: true});
    if (!fresh) return;
    renderCurrent(); if (value.investigation_id === selectedHistory) renderDetail();
    if (value.type === 'investigation.finished') {
      void loadDetail(value.investigation_id, value.investigation_id === selectedHistory); void loadHistory();
    }
    // Neither started nor finished changes active ID. Only state owns it.
  }));
  events.addEventListener('ping', receive(value => { if (!Number.isFinite(Date.parse(value.server_now))) throw new Error('ping 缺少 server_now。'); }));
  events.onerror = () => { if (stream === events) reconnect(); };
}
function reconnectLogs() {
  logStream?.close(); logStream = null;
  if (disposed || document.hidden || logRetry) return;
  error('monitor', 'Monitor log 連線中斷；調查串流仍獨立運作，重連不補送缺漏 log。');
  logRetry = setTimeout(() => { logRetry = null; connectLogs(); }, logDelay);
  logDelay = Math.min(8000, logDelay * 2);
}
function connectLogs() {
  if (disposed || document.hidden || logStream) return;
  lastLogActivity = Date.now();
  const events = new EventSource('/events'); logStream = events;
  const receive = handler => event => {
    if (logStream !== events || disposed) return;
    try {
      handler(JSON.parse(event.data)); lastLogActivity = Date.now(); logDelay = 1000; error('monitor');
    } catch (e) { error('monitor', `Monitor log 資料錯誤：${e.message}`); }
  };
  events.addEventListener('log', receive(log => {
    if (log.schema_version !== 'nightwatch.log.v1' || typeof log.event_id !== 'string' || !log.event_id || typeof log.monitor_id !== 'string' || !log.monitor_id || typeof log.message !== 'string' || !Number.isFinite(Date.parse(log.occurred_at)) || !['TRACE', 'DEBUG', 'INFO', 'WARN', 'ERROR', 'FATAL'].includes(log.level) || !Array.isArray(log.refs?.node_ids) || log.refs.node_ids.some(id => typeof id !== 'string')) throw new Error('不符合 nightwatch.log.v1。');
    logs.set(JSON.stringify([log.monitor_id, log.event_id]), log); renderCurrent();
  }));
  events.addEventListener('ping', receive(value => {
    // Monitor /events sends an empty heartbeat, independently of investigation SSE.
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('ping 必須是物件。');
    if ('server_now' in value && !Number.isFinite(Date.parse(value.server_now))) throw new Error('ping 的 server_now 格式錯誤。');
  }));
  events.onerror = () => { if (logStream === events) reconnectLogs(); };
}
function pauseStreams() {
  stream?.close(); logStream?.close(); stream = logStream = null;
  clearTimeout(streamRetry); clearTimeout(logRetry); streamRetry = logRetry = null;
}
async function resumeWorkspace() {
  if (disposed || document.hidden) return;
  // Finish ordinary reads before reserving two HTTP connections for SSE.
  await refresh();
  if (disposed || document.hidden) return;
  connect(); connectLogs();
}
function visibilityChanged() {
  if (document.hidden) {
    pauseStreams();
    connection('背景分頁暫停即時連線');
  } else {
    void resumeWorkspace();
  }
}
function dispose() {
  disposed = true; pauseStreams(); clearInterval(heartbeatTimer);
  clearInterval(snapshotPoll); cancelSnapshot(); snapshotIndexController?.abort();
  document.removeEventListener('visibilitychange', visibilityChanged);
}

export function startWorkspace() {
  document.title = 'NightWatch · 調查工作台';
  $('nav-usage').hidden = false;
  $('nav-incidents').href = '#investigations'; $('nav-incidents').innerHTML = '調查歷史 <span id="nav-count">—</span>';
  $('source').value = 'live'; $('source').onchange = event => { const url = new URL(location.href); url.searchParams.set('source', event.target.value); location.href = url.href; };
  $('investigation-phase').parentElement.querySelector('h2').textContent = '目前調查';
  $('show-report').textContent = '目前調查報告';
  $('active-phase').previousElementSibling.textContent = '目前調查';
  $('graph-viewport').parentElement.querySelector('.assessment-legend').textContent = '健康依後端觀測；同一節點位置固定，位置不表示呼叫順序。';
  $('page-title').closest('.page-heading').insertAdjacentHTML('afterend', '<div class="investigation-actions"><button id="start-investigation" disabled>開始調查</button><span id="submission-status" role="status">僅在按下按鈕後開始；重新整理不會建立調查。</span></div>');
  $('snapshot-at').previousElementSibling.textContent = '顯示快照時間 · 台灣';
  $('graph-timeline').hidden = false;
  $('snapshot-range').oninput = event => { if (timelineBounds) selectSnapshot(timelineBounds.from + Number(event.target.value)); };
  $('snapshot-range').onchange = event => { if (timelineBounds) selectSnapshot(timelineBounds.from + Number(event.target.value), true); };
  $('snapshot-range').onkeydown = event => {
    if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
      event.preventDefault(); stepSnapshot(['ArrowLeft', 'ArrowDown'].includes(event.key) ? -1 : 1);
    }
  };
  $('snapshot-prev').onclick = () => stepSnapshot(-1);
  $('snapshot-next').onclick = () => stepSnapshot(1);
  $('graph-live').onclick = returnToLive;
  snapshotPoll = setInterval(() => void loadSnapshotIndex(), 5000);
  $('incidents-view').querySelector('thead tr').innerHTML = '<th>調查</th><th>狀態</th><th>原因</th><th>開始時間 · UTC</th><th>結果</th><th>報告</th>';
  $('incident-search').placeholder = '搜尋調查 ID、摘要或狀態…';
  document.querySelector('label[for="incident-search"]')?.setAttribute('aria-label', '搜尋調查');
  const searchLabel = $('incident-search').previousElementSibling;
  if (searchLabel?.tagName === 'LABEL') searchLabel.textContent = '搜尋調查';
  $('incidents-view').querySelector('[role="group"]').setAttribute('aria-label', '調查狀態篩選');
  $('investigation-phase').closest('aside').setAttribute('aria-label', '目前調查');
  $('list-count').insertAdjacentHTML('afterend', '<button id="more-investigations" hidden>載入更多調查</button>');
  $('more-investigations').onclick = () => loadHistory(true);
  $('incident-search').oninput = renderHistory;
  document.querySelectorAll('[data-status]').forEach(button => button.onclick = () => {
    filter = button.dataset.status;
    document.querySelectorAll('[data-status]').forEach(b => b.setAttribute('aria-pressed', String(b === button))); renderHistory();
  });
  document.querySelectorAll('[data-event-source]').forEach(button => {
    button.onclick = () => { journalFilter = button.dataset.eventSource; document.querySelectorAll('[data-event-source]').forEach(b => b.setAttribute('aria-pressed', String(b === button))); renderCurrent(); };
  });
  const switchTab = report => { $('journal-panel').hidden = report; $('investigation-report').hidden = !report; $('show-journal').setAttribute('aria-pressed', String(!report)); $('show-report').setAttribute('aria-pressed', String(report)); };
  $('show-journal').onclick = () => switchTab(false); $('show-report').onclick = () => switchTab(true);
  $('node-search').oninput = renderGraph; $('health-filter').onchange = renderGraph;
  $('zoom-in').onclick = () => { fit = false; zoom = Math.min(1.5, zoom + .1); renderGraph(); };
  $('zoom-out').onclick = () => { fit = false; zoom = Math.max(.5, zoom - .1); renderGraph(); };
  $('fit').onclick = () => { fit = true; renderGraph(); };
  $('refresh').onclick = refresh; $('start-investigation').onclick = startInvestigation;
  document.querySelector('footer span:last-child').textContent = 'NightWatch · 調查由後端執行';
  try {
    const value = sessionStorage.getItem(storageKey);
    if (value) {
      const saved = JSON.parse(value);
      if (typeof saved.request_id !== 'string' || saved.trigger?.source !== 'manual' || saved.trigger?.reason !== '調查目前服務異常') throw new Error('待重試操作內容不合法，無法安全重送。');
      pendingRequest = saved; $('submission-status').textContent = `有尚未確認的操作 ${saved.request_id}；重試沿用相同 ID。`;
    }
  } catch (e) { error('storage', e.message); }
  window.addEventListener('hashchange', route);
  window.addEventListener('resize', () => { if (fit) renderGraph(); });
  window.addEventListener('pagehide', dispose, {once: true});
  document.addEventListener('visibilitychange', visibilityChanged);
  window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
  lastActivity = lastLogActivity = Date.now();
  heartbeatTimer = setInterval(() => {
    const age = Date.now() - lastActivity;
    if (age >= 15000 && stream) reconnect(); else if (age >= 6000 && stream) connection('調查連線延遲', 'stale');
    if (Date.now() - lastLogActivity >= 15000 && logStream) reconnectLogs();
  }, 1000);
  renderCurrent(); route(); void resumeWorkspace();
}
