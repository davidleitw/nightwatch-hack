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
const icons = {
  agent: '<rect x="5" y="7" width="14" height="13" rx="4"/><path d="M12 3v4M9 12h.01M15 12h.01M9 16h6M2 11v5M22 11v5"/>',
  chat: '<path d="M21 11a8 8 0 0 1-8 8H7l-4 3V11a9 9 0 0 1 18 0Z"/><path d="M8 10h8M8 14h5"/>',
  tool: '<path d="m14 6 4 4M6 14l4 4M14 3a6 6 0 0 0-7 7L3 14a3 3 0 0 0 7 7l4-4a6 6 0 0 0 7-7l-4 4-7-7Z"/>',
  report: '<path d="M14 2H5v20h14V7Z M14 2v5h5M8 12h8M8 16h8"/>',
  history: '<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v5l3 2"/>',
  token: '<path d="m12 2 9 5v10l-9 5-9-5V7ZM3 7l9 5 9-5M12 12v10"/>',
  cache: '<path d="M20 7a9 9 0 0 0-15-2L2 8m0-6v6h6M4 17a9 9 0 0 0 15 2l3-3m0 6v-6h-6"/>',
  pulse: '<path d="M2 12h4l3-8 6 16 3-8h4"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  alert: '<path d="m12 3 10 18H2ZM12 9v5M12 17h.01"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  down: '<path d="M12 4v16m-6-6 6 6 6-6"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 22v-3a8 8 0 0 1 16 0v3"/>',
  thinking: '<path d="M9 18h6M10 22h4M8 14a7 7 0 1 1 8 0l-1 4H9Z"/>',
};
const icon = name => `<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.chat}</svg>`;
const reportHref = id => `#investigations/${encodeURIComponent(id)}`;
const chatHref = id => `${reportHref(id)}/chat`;
const shortTime = value => Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleTimeString('zh-TW', {hour12: false}) : '—';
const store = new InvestigationStore();
const errors = new Map(), details = new Map(), contexts = new Map(), eventLoads = new Map(), logs = new Map();
let graph = null, graphReceivedAt = null, selectedNode = null, zoom = 1, fit = true, layoutSignature = '';
let stream = null, logStream = null, streamRetry = null, logRetry = null, heartbeatTimer = null, streamDelay = 1000, logDelay = 1000;
let lastActivity = 0, lastLogActivity = 0, disposed = false, graphGeneration = 0;
let history = [], nextBefore = null, historyBusy = false, historyAgain = false, selectedHistory = null, detailGeneration = 0, filter = 'all';
let selectedDetailTab = 'report', lastChatId = null, messageReplay = true;
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
  const previous = activeId(), previousUsage = usageId();
  if (!store.acceptState(value, options)) return;
  if (options.acceptGraph !== false && value.graph !== null) acceptGraph(value.graph, value.graph_received_at);
  error('graph', value.graph_error ? `服務拓樸來源：${value.graph_error}。保留最後取得的快照。` : value.graph === null ? '目前沒有可用的服務拓樸。調查歷史仍可讀取。' : null);
  error('state');
  updateStartButton(); renderCurrent();
  if (activeId()) {
    if (previous !== activeId() || !store.events.has(activeId())) void loadEvents(activeId());
  }
  const shown = currentChatId();
  if (shown && shown !== activeId()) {
    if (!store.events.has(shown)) void loadEvents(shown);
  }
  if (usageId() && (previous !== activeId() || previousUsage !== usageId() || !details.has(usageId()))) void loadUsage();
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

function reportCard(id, submitted = null) {
  const detail = details.get(id), saved = detail?.report;
  const structured = saved?.investigation_report ?? saved?.agent_report ?? submitted;
  if (!saved && !structured) return '';
  const title = structured ? '調查報告已產生' : '調查結案紀錄';
  return `<a class="chat-report-card" href="${reportHref(id)}">${icon('report')}<span><small>${structured ? 'INVESTIGATION REPORT' : 'INVESTIGATION RECORD'}</small><strong>${title}</strong><span>${esc(structured?.summary_zh || saved?.summary_zh || '查看調查結果與引用證據')}</span><b>開啟${structured ? '完整報告' : '結案紀錄'} ${icon('arrow')}</b></span>${icon('arrow')}</a>`;
}
function activityItems(id, terminal = false, sourceFilter = 'all') {
  const events = store.activity(id);
  const rows = pairedActivity(events, terminal).filter(row => row.event?.type !== 'agent.usage').filter(row => sourceFilter === 'all' || (row.kind === 'tool' || row.event?.type.startsWith('agent.') ? sourceFilter === 'agent' : sourceFilter === 'system'));
  const items = rows.map(row => {
    if (row.kind === 'event') {
      const e = row.event, started = e.type === 'investigation.started';
      if (e.type === 'agent.reasoning_status') {
        return {key: `message:${id}:${e.seq}`, at: e.at, html: `<article class="chat-message agent-message"><div class="chat-avatar">${icon('thinking')}</div><div class="chat-message-body"><div class="chat-meta"><strong>模型推理</strong><time title="${esc(at(e.at))}">${esc(shortTime(e.at))}</time></div><p class="chat-tool-description">模型已進行推理，此次 API 未提供可讀摘要。</p></div></article>`};
      }
      if (e.type === 'agent.output' || e.type === 'agent.thinking_summary') {
        const thinking = e.type === 'agent.thinking_summary';
        return {key: `message:${id}:${e.seq}`, at: e.at, html: `<article class="chat-message agent-message"><div class="chat-avatar">${icon(thinking ? 'thinking' : 'agent')}</div><div class="chat-message-body"><div class="chat-meta"><strong>${thinking ? '推理摘要' : 'NightWatch Agent'}</strong><time title="${esc(at(e.at))}">${esc(shortTime(e.at))}</time></div>${thinking ? `<details class="chat-thinking" data-preserve="thinking:${esc(id)}:${e.seq}"><summary>${icon('thinking')}查看公開推理摘要</summary><div class="chat-bubble">${esc(e.payload.text)}</div></details>` : `<div class="chat-bubble">${esc(e.payload.text)}</div>`}</div></article>`};
      }
      const label = started ? '調查請求' : '調查結束';
      const text = started ? e.payload.trigger?.reason || details.get(id)?.trigger?.reason || '開始調查目前服務狀態' : e.payload.reason || outcomeText[e.payload.outcome] || statusText[e.payload.status] || e.type;
      return {key: `event:${id}:${e.seq}`, at: e.at, html: `<article class="chat-message ${started ? 'user-message' : 'system-message'}"><div class="chat-avatar">${icon(started ? 'user' : 'check')}</div><div class="chat-message-body"><div class="chat-meta"><strong>${label}</strong><time title="${esc(at(e.at))}">${esc(shortTime(e.at))}</time></div><div class="chat-bubble ${e.payload.status === 'failed' || e.payload.status === 'interrupted' ? 'chat-failed' : ''}">${esc(text)}</div><details data-preserve="event:${esc(id)}:${e.seq}"><summary>事件明細</summary>${raw(e.payload)}</details></div></article>`};
    }
    const end = row.finished, start = row.started, failed = ['failed', 'incomplete'].includes(row.status);
    const name = start?.payload.tool ?? end?.payload.tool ?? end?.payload.evidence?.tool ?? '工具';
    const label = {running: '執行中', recorded: '已取得證據', reported: '報告已提交', failed: '執行失敗', incomplete: '未收到結果'}[row.status];
    const evidence = end?.payload.evidence;
    const description = row.status === 'recorded' ? evidence?.summary_zh || (evidence === undefined ? '成功事件未提供 evidence。' : '已保存工具回傳的證據。') : failed ? end?.payload.error?.message_zh || end?.payload.error || end?.payload.reason || label : row.status === 'reported' ? 'submit_report 已回傳報告，點擊下方卡片閱讀。' : '等待工具回傳結果…';
    const contents = start ? `<h4>呼叫參數</h4>${raw(start.payload.args ?? start.payload)}` : '<p class="failing">未收到工具開始事件。</p>';
    const result = end ? `<h4>${failed ? '失敗內容' : row.status === 'reported' ? '提交結果' : '工具結果'}</h4>${raw(row.status === 'recorded' ? evidence : row.status === 'reported' ? end.payload.report : end.payload)}` : '';
    return {key: `tool:${id}:${row.call_id}`, at: start?.at ?? end?.at, html: `<article class="chat-message tool-message"><div class="chat-avatar">${icon('agent')}</div><div class="chat-message-body"><div class="chat-meta"><strong>NightWatch Agent</strong><time title="${esc(at(start?.at ?? end?.at))}">${esc(shortTime(start?.at ?? end?.at))}</time></div><details class="chat-tool ${failed ? 'chat-failed' : ''}" data-preserve="tool:${esc(id)}:${esc(row.call_id)}"><summary>${icon(row.status === 'running' ? 'pulse' : failed ? 'alert' : 'tool')}<span class="chat-tool-name">${esc(name)}</span><span class="chat-tool-status ${row.status === 'running' ? 'is-running' : ''}">${label}</span></summary><div class="chat-tool-content"><p class="report-note">${esc(row.call_id)}</p>${contents}${result}</div></details><p class="chat-tool-description ${failed ? 'failing' : ''}">${esc(typeof description === 'string' ? description : JSON.stringify(description))}</p>${row.status === 'reported' ? reportCard(id, end.payload.report) : ''}</div></article>`};
  });
  if (['all', 'agent'].includes(sourceFilter) && !events.some(e => e.type === 'report.submitted') && details.get(id)?.report) items.push({key: `report:${id}`, at: details.get(id).closed_at, html: reportCard(id)});
  return items;
}
function activityHTML(id, terminal = false, sourceFilter = 'all') {
  return activityItems(id, terminal, sourceFilter).map(item => item.html).join('') || '<div class="chat-empty">' + icon('chat') + '<strong>等候調查紀錄</strong><p>取得的訊息與工具結果會顯示在這裡。</p></div>';
}
// Reuse unchanged entries so SSE updates do not close evidence or steal focus.
function updateChat(container, items, follow = false) {
  const scroller = follow ? $('journal-panel') : null;
  const nearBottom = scroller && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 70;
  const existing = new Map([...container.children].map(node => [node.dataset.chatKey, node]));
  let changed = false;
  for (let index = 0; index < items.length; index++) {
    const item = items[index];
    let node = existing.get(item.key);
    if (!node) { node = document.createElement('div'); node.dataset.chatKey = item.key; changed = true; }
    if (node._chatHTML !== item.html) {
      const open = new Set([...node.querySelectorAll('details[open][data-preserve]')].map(el => el.dataset.preserve));
      const active = node.contains(document.activeElement) ? document.activeElement : null;
      const focused = active ? {id: active.id, href: active.getAttribute('href'), hrefIndex: [...node.querySelectorAll('a[href]')].filter(el => el.getAttribute('href') === active.getAttribute('href')).indexOf(active), details: active.closest('details')?.dataset.preserve} : null;
      node.innerHTML = item.html; node._chatHTML = item.html; changed = true;
      node.querySelectorAll('details[data-preserve]').forEach(el => { if (open.has(el.dataset.preserve)) el.open = true; if (focused?.details === el.dataset.preserve) el.querySelector('summary')?.focus({preventScroll: true}); });
      if (focused?.id) [...node.querySelectorAll('[id]')].find(el => el.id === focused.id)?.focus({preventScroll: true});
      else if (focused?.href) [...node.querySelectorAll('a[href]')].filter(el => el.getAttribute('href') === focused.href)[focused.hrefIndex]?.focus({preventScroll: true});
    }
    if (container.children[index] !== node) container.insertBefore(node, container.children[index] || null);
    existing.delete(item.key);
  }
  existing.forEach(node => { node.remove(); changed = true; });
  if (scroller && changed) {
    if (nearBottom) scroller.scrollTop = scroller.scrollHeight;
    $('chat-latest').hidden = nearBottom || scroller.scrollHeight <= scroller.clientHeight;
  }
}
function normalizedUsage(usage) {
  if (!usage || typeof usage !== 'object' || Array.isArray(usage)) return usage;
  return {...usage, cached_tokens: usage.cache_read_tokens !== undefined ? usage.cache_read_tokens : usage.cached_tokens, calls: usage.requests !== undefined ? usage.requests : usage.calls};
}
function usageStrip(usage) {
  const {u, tools, problems} = usageViewData(null, usage);
  return `<div class="usage-strip"><span title="輸入與輸出 token 加總">${icon('token')}<span>Token <strong>${number(u.total)}</strong></span></span><span title="快取讀取占輸入 token 比例">${icon('cache')}<span>Cache <strong>${number(u.rate, '%', 100)}</strong></span></span><span title="模型請求次數">${icon('pulse')}<span>請求 <strong>${number(u.calls)}</strong></span></span><span title="後端工具用量，不含 submit_report 報告提交">${icon('tool')}<span>工具 <strong>${number(tools)}</strong></span></span></div>${problems.length ? `<p class="failing usage-strip-error">${esc(problems.join(' '))}</p>` : ''}`;
}
function usageFor(id) {
  const detail = details.get(id);
  const latest = store.activity(id).findLast(e => e.type === 'agent.usage');
  return latest && latest.seq > (detail?.event_seq ?? -1) ? latest.payload.usage : detail?.usage ?? latest?.payload.usage ?? (id === activeId() ? activeSummary()?.usage : undefined);
}
function renderUsage() { $('agent-usage').innerHTML = usageStrip(usageFor(usageId())); }
function usageId() { return activeId() || store.state?.last_completed_investigation_id; }
function usageViewData(id = usageId(), usage = usageFor(id)) {
  const detail = details.get(id);
  const u = readUsage(normalizedUsage(usage));
  const problems = u.problems.map(message => message.replaceAll('cached_tokens', usage?.cache_read_tokens !== undefined ? 'cache_read_tokens' : 'cached_tokens').replaceAll('calls', usage?.requests !== undefined ? 'requests' : 'calls'));
  const extra = key => {
    const value = usage?.[key];
    if (value === undefined) return null;
    if (!Number.isSafeInteger(value) || value < 0) { problems.push(`${key} 必須是非負安全整數。`); return null; }
    return value;
  };
  const writes = extra('cache_write_tokens'), tools = extra('tool_calls');
  const reported = [u.input_tokens, u.output_tokens, u.cached_tokens, u.calls, writes, tools].some(value => value !== null);
  const stateLabel = !store.state ? '等待調查狀態' : !id ? '尚無調查' : !detail && usage == null ? '尚未取得用量' : problems.length ? '資料異常' : reported ? '已回報' : '尚未回報';
  return {id, usage, u, problems, writes, tools, reported, stateLabel};
}
function renderUsagePage() {
  const view = $('usage-view');
  if (view.hidden) return;
  const {id, usage, u, problems, writes, tools, reported, stateLabel} = usageViewData();
  const expanded = view.querySelector('details')?.open;
  const trend = (title, unit) => `<article class="usage-view-chart"><div class="usage-view-chart-heading"><h2>${title}</h2><span>${unit}</span></div><div class="usage-view-chart-empty"><svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.25" aria-hidden="true"><path d="M4 4v16h16"/><circle cx="14" cy="10" r="5"/><path d="M14 7v3l2 1"/></svg><strong>尚無趨勢資料</strong><p>目前只有調查累計值，尚無各時間點的用量。</p></div></article>`;
  view.innerHTML = `<div class="usage-view-context"><div><span class="usage-view-kicker">${activeId() ? '進行中的調查' : '最近一次調查'}</span><p>${id ? esc(id) : '開始調查後，這裡會顯示 Agent 的用量。'}</p></div><div class="usage-view-context-actions"><span class="usage-view-status ${problems.length ? 'usage-view-invalid' : ''}">${stateLabel}</span>${id ? `<a href="#investigations/${encodeURIComponent(id)}">查看調查 →</a>` : ''}<a href="#topology">返回服務拓樸 →</a></div></div>
    <div class="usage-view-metrics">
      <article><h2>總 token</h2><strong>${number(u.total)}</strong><p>輸入 + 輸出</p></article>
      <article><h2>Prompt cache 命中率</h2><strong>${number(u.rate, '%', 100)}</strong><p>${u.input_tokens === 0 ? '尚無輸入，命中率不適用' : '快取讀取 / 全部輸入'}</p><div class="usage-view-meter" ${u.rate === null ? 'aria-hidden="true"' : `role="meter" aria-label="Prompt cache 命中率" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${u.rate * 100}"`}><span style="width:${u.rate === null ? 0 : u.rate * 100}%"></span></div></article>
      <article><h2>模型請求</h2><strong>${number(u.calls)} <small>次</small></strong><p>本次調查的模型請求數</p></article>
      <article><h2>工具呼叫</h2><strong>${number(tools)} <small>次</small></strong><p>後端工具用量，不含報告提交</p></article>
    </div>
    ${problems.length ? `<p class="usage-view-error" role="alert">${esc(problems.join(' '))}</p>` : !reported ? '<p class="usage-view-note">尚未取得用量數字；「—」不代表 0。若讀取失敗，請查看上方錯誤並按「更新資料」。</p>' : ''}
    <div class="usage-view-section-title"><h2>用量趨勢</h2><span>本次調查</span></div><div class="usage-view-charts">${trend('Token 使用量', 'tokens')}${trend('Prompt cache 命中率', '%')}</div>
    <section class="usage-view-breakdown"><div class="usage-view-section-title"><h2>Token 明細</h2><span>後端累計</span></div><dl><div><dt>輸入 token</dt><dd>${number(u.input_tokens)}</dd></div><div><dt>輸出 token</dt><dd>${number(u.output_tokens)}</dd></div><div><dt>其中快取讀取</dt><dd>${number(u.cached_tokens)}</dd></div><div><dt>快取寫入</dt><dd>${number(writes)}</dd></div></dl><p class="usage-view-note">快取讀取包含於輸入，總量不重複加總。命中率代表輸入 token 的快取比例，與回答正確率無關。</p></section>
    <details class="usage-view-raw" ${expanded ? 'open' : ''}><summary>統計口徑與原始值</summary><p>範圍為上方這次調查，不代表帳號總用量。總 token = input_tokens + output_tokens。Prompt cache 命中率 = 快取讀取 ÷ input_tokens × 100%。未回報欄位顯示「—」，實際回報 0 才顯示 0。</p><p>數字依最新累計用量事件或調查詳情更新，可按「更新資料」重新讀取。</p>${raw(usage)}</details>`;
}
const usageLoads = new Map();
async function loadUsage() {
  renderUsage();
  renderUsagePage();
  const id = usageId();
  if (!id) return;
  if (usageLoads.has(id)) return usageLoads.get(id);
  const task = loadDetail(id, false);
  usageLoads.set(id, task);
  try { await task; }
  finally { usageLoads.delete(id); }
}
function reportHTML(detail, id = detail?.id) {
  const submitted = store.activity(id).findLast(e => e.type === 'report.submitted')?.payload.report;
  if (!detail && !submitted) return '<div class="chat-empty">' + icon('report') + '<strong>正在讀取調查報告</strong></div>';
  const report = detail?.report, agent = report?.investigation_report ?? report?.agent_report ?? submitted;
  if (!report && !agent) return `<div class="chat-empty">${icon(detail.status === 'running' ? 'pulse' : 'alert')}<strong>${detail.status === 'running' ? '調查進行中，報告尚未產生' : '尚無提交的調查報告'}</strong><p>${esc(detail.summary_zh || '可返回對話查看工具執行狀態。')}</p></div>`;
  const list = (title, values) => Array.isArray(values) && values.length ? `<section class="report-section"><h3>${title}</h3><ul>${values.map(value => `<li>${esc(value)}</li>`).join('')}</ul></section>` : '';
  const chips = values => Array.isArray(values) ? values.map(value => `<span class="report-chip">${esc(value)}</span>`).join('') : '';
  const findings = Array.isArray(agent?.findings) ? agent.findings : [];
  const hypotheses = Array.isArray(agent?.hypotheses) ? agent.hypotheses : [];
  const conclusion = {supported: '調查結論有證據支持', inconclusive: '證據不足，尚無定論', no_incident_observed: '未觀測到事故', root_cause_identified: '已識別根因'}[agent?.conclusion] || agent?.conclusion || outcomeText[report?.outcome] || '調查結果';
  return `<div class="report-lead"><span class="report-eyebrow">${icon('report')} INVESTIGATION REPORT</span><h2>${esc(conclusion)}</h2><p class="report-summary">${esc(agent?.summary_zh || report?.summary_zh || agent?.root_cause?.summary_zh || '未提供摘要')}</p><div class="report-meta"><span>${icon('history')}${esc(at(report?.started_at || detail?.started_at))}</span><span>${icon('check')}${report ? '已保存結案紀錄' : '工具已提交，等待結案'}</span></div></div>
  ${findings.length ? `<section class="report-section"><h3>${icon('pulse')} 調查發現 <span>${findings.length}</span></h3>${findings.map((finding, i) => `<article class="report-finding"><span class="finding-number">${String(i + 1).padStart(2, '0')}</span><div><p>${esc(finding.summary_zh)}</p><div class="report-chips">${chips(finding.node_ids)}${chips(finding.evidence_ids)}</div></div></article>`).join('')}</section>` : ''}
  ${agent?.root_cause ? `<section class="report-section"><h3>根因</h3><p>${esc(agent.root_cause.summary_zh)}</p><div class="report-chips">${chips([agent.root_cause.node, agent.root_cause.mechanism])}</div><p>信心 ${number(agent.confidence, '%', 100)}</p></section>` : ''}
  ${hypotheses.length ? `<section class="report-section"><h3>${icon('thinking')} 假說與不確定性</h3>${hypotheses.map(h => `<article class="report-hypothesis"><h4>${esc(h.cause_zh)}</h4><p>${esc(h.uncertainty_zh)}</p><div class="report-chips">${chips(h.node_ids)}</div><p class="report-note">支持證據 ${esc(h.supporting_evidence_ids?.join('、') || '未列出')} · 反向證據 ${esc(h.counterevidence_ids?.join('、') || '未列出')}</p></article>`).join('')}</section>` : ''}
  ${list('限制與觀測缺口', [...new Set([...(Array.isArray(report?.limitations) ? report.limitations : []), ...(Array.isArray(agent?.limitations) ? agent.limitations : [])])])}
  ${list('建議下一步', agent?.next_steps)}
  <section class="report-section"><h3>${icon('tool')} 引用證據</h3><div class="report-chips">${chips(report?.evidence_ids ?? agent?.cited_evidence_ids)}</div>${Array.isArray(detail?.evidence) ? detail.evidence.map(e => `<details class="report-evidence" data-preserve="evidence:${esc(e.id)}"><summary><span>${esc(e.id)}</span> ${esc(e.summary_zh || e.tool)}</summary>${raw(e)}</details>`).join('') : '<p class="report-note">尚未取得保存的證據。</p>'}</section>
  <details class="report-raw" data-preserve="report-raw"><summary>完整報告原始資料</summary>${raw(report ?? agent)}</details><p class="report-note">調查完成不表示服務已恢復；服務健康仍以觀測資料為準。</p>`;
}
function currentChatId() { return activeId() || store.state?.last_completed_investigation_id || null; }
function renderCurrent() {
  const id = currentChatId(), detail = id ? details.get(id) : null, summary = activeSummary() || detail;
  const terminal = summary ? summary.status !== 'running' : false;
  $('active-phase').textContent = activeId() ? '調查中' : store.state ? '無進行中調查' : '等待狀態';
  $('investigation-phase').innerHTML = summary ? badge(summary) : '';
  $('investigation-summary').innerHTML = id ? `<span class="chat-session-label">${activeId() ? '<i class="live-dot"></i>目前調查' : icon('history') + '最近一次調查'}</span><span class="chat-session-id" title="${esc(id)}">${esc(id)}</span>` : `<span class="chat-session-label">${store.state ? '開始調查，讓 Agent 蒐集證據並產生報告。' : '等待調查狀態…'}</span>`;
  renderUsage();
  const items = id && journalFilter !== 'monitor' ? activityItems(id, terminal, journalFilter) : [];
  if (journalFilter === 'all' || journalFilter === 'monitor') {
    for (const log of logs.values()) items.push({key: `monitor:${log.monitor_id}:${log.event_id}`, at: log.occurred_at, html: `<article class="chat-message monitor-message"><div class="chat-avatar">${icon('pulse')}</div><div class="chat-message-body"><div class="chat-meta"><strong>Monitor</strong><span>${esc(log.level)}</span><time>${esc(shortTime(log.occurred_at))}</time></div><div class="chat-bubble">${esc(log.message)}</div><div class="journal-links">${log.refs.node_ids.map(node => `<button class="node-link" data-locate-node="${esc(node)}">${esc(node)} ↗</button>`).join('')}</div><details data-preserve="log:${esc(log.monitor_id)}:${esc(log.event_id)}"><summary>原始 log · ${esc(log.monitor_id)}</summary>${raw(log)}</details></div></article>`});
    // Merge monitor observations by timestamp without changing investigation seq order.
    const monitorItems = items.filter(item => item.key.startsWith('monitor:')).sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
    const agentItems = items.filter(item => !item.key.startsWith('monitor:'));
    items.length = 0;
    for (const item of agentItems) { while (monitorItems.length && Date.parse(monitorItems[0].at) <= Date.parse(item.at)) items.push(monitorItems.shift()); items.push(item); }
    items.push(...monitorItems);
  }
  if (!items.length) items.push({key: 'empty', html: `<div class="chat-empty">${icon('agent')}<strong>${id ? '等候調查紀錄' : '準備好開始調查'}</strong><p>${id ? '目前沒有符合來源的訊息。' : 'Agent 的回覆、工具取證與報告會出現在這裡。'}</p><a href="#investigations">${icon('history')} 瀏覽調查歷史</a></div>`});
  const changedId = lastChatId !== id;
  if (changedId) { $('journal-entries').replaceChildren(); lastChatId = id; }
  updateChat($('journal-entries'), items, true);
  if (changedId) { $('journal-panel').scrollTop = $('journal-panel').scrollHeight; $('chat-latest').hidden = true; }
  $('journal-count').textContent = `${id ? store.activity(id).length : 0} 個事件 · ${logs.size} 筆 log`;
  $('journal-note').textContent = messageReplay ? '文字與推理摘要依模型實際回覆呈現；工具結果可展開。Monitor 斷線不補送。' : '此來源僅提供工具紀錄，未提供 Agent 文字回放。Monitor 斷線不補送。';
  $('show-report').disabled = !id;
  $('show-report').onclick = () => { if (id) location.hash = reportHref(id); };
  $('investigation-report').hidden = true;
  const latest = store.state?.last_completed_investigation_id;
  $('current-incident').innerHTML = `<div><h3>${activeId() ? '調查由後端執行，關閉頁面不會取消' : '目前沒有進行中的調查'}</h3><p>調查結論與服務健康分開顯示。</p></div><a href="${latest ? reportHref(latest) : '#investigations'}">${icon(latest ? 'report' : 'history')}${latest ? '查看最近一次報告' : '查看調查歷史'} ${icon('arrow')}</a>`;
  if (!store.state) $('current-incident').innerHTML = '<div><h3>尚未取得調查狀態</h3><p>請查看上方連線錯誤，恢復後按「更新資料」。</p></div>';
}

async function loadEvents(id) {
  if (eventLoads.has(id)) return eventLoads.get(id);
  const task = (async () => {
    let after = 0;
    try {
      do {
        const path = `/api/investigations/${encodeURIComponent(id)}/events?after=${after}&limit=100`;
        let page;
        try { page = await requestJSON(path + (messageReplay ? '&include_messages=1' : '')); }
        catch (e) {
          if (!messageReplay || e.status !== 400) throw e;
          page = await requestJSON(path); // Older control rejects only the new query.
          messageReplay = false;
        }
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
  $('incident-rows').innerHTML = rows.map(item => `<tr><td>${esc(item.id)}</td><td>${badge(item)}</td><td>${esc(item.trigger?.reason || '—')}</td><td>${esc(at(item.started_at))}</td><td>${esc(outcomeText[item.outcome] || item.outcome || '—')}</td><td><div class="history-links"><a href="${chatHref(item.id)}">${icon('chat')}對話</a><a href="${reportHref(item.id)}">${icon('report')}報告</a></div></td></tr>`).join('');
  $('list-empty').hidden = Boolean(selectedHistory) || rows.length > 0;
  $('list-empty').textContent = errors.has('history') ? '無法取得調查歷史，請查看上方錯誤。' : '目前沒有符合條件的調查。';
  $('list-count').textContent = `顯示 ${rows.length} / 已載入 ${history.length} 件調查；包含失敗、中斷與未解決。`;
  $('more-investigations').hidden = Boolean(selectedHistory) || nextBefore === null;
}
function renderDetail() {
  $('incident-detail').hidden = !selectedHistory;
  if (!selectedHistory) return;
  const detail = details.get(selectedHistory);
  const heading = `<div class="detail-navigation"><a href="#investigations">${icon('history')} 調查歷史</a><span>/</span><span>${esc(selectedHistory)}</span><a href="#topology">返回工作台 ${icon('arrow')}</a></div><div class="detail-title"><div><span class="report-eyebrow">NIGHTWATCH / INVESTIGATION</span><h2>${detail?.created_seq ? `調查 #${esc(detail.created_seq)}` : '調查紀錄'}</h2></div>${detail ? badge(detail) : ''}</div><div class="detail-tabs"><a href="${reportHref(selectedHistory)}" aria-current="${selectedDetailTab === 'report' ? 'page' : 'false'}">${icon('report')}報告</a><a href="${chatHref(selectedHistory)}" aria-current="${selectedDetailTab === 'chat' ? 'page' : 'false'}">${icon('chat')}調查對話</a></div>`;
  const submitted = store.activity(selectedHistory).some(e => e.type === 'report.submitted');
  const content = !detail && !(submitted && selectedDetailTab === 'report') ? '<div class="chat-empty">' + icon('report') + '<strong>正在讀取調查</strong><p>若讀取失敗，請按上方「更新資料」。</p></div>' : selectedDetailTab === 'report' ? reportHTML(detail, selectedHistory) : `<div class="history-usage">${usageStrip(usageFor(detail.id))}</div><div class="history-chat">${activityHTML(detail.id, detail.status !== 'running')}</div><section class="report-section"><h3>保存的上下文</h3><p class="report-note">${detail.context_available ? detail.context_complete ? '後端標記上下文完整。' : '上下文不完整；不是完整模型對話。' : '後端尚未保存上下文。'}</p><button id="load-context" ${detail.context_available ? '' : 'disabled'}>讀取保存的上下文</button><div id="saved-context"></div></section>`;
  updateChat($('incident-detail'), [{key: `detail:${selectedHistory}:${selectedDetailTab}`, html: `${heading}<div class="report-document">${content}</div>`}]);
  if ($('load-context')) $('load-context').onclick = () => loadContext(detail.id);
  if ($('saved-context') && contexts.has(detail.id)) renderContext(contexts.get(detail.id));
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
    if (selectedHistory !== id || generation !== detailGeneration || !$('saved-context')) return;
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
  document.querySelector('.investigation-actions').hidden = usage || list;
  $('nav-usage').setAttribute('aria-current', usage ? 'page' : 'false');
  $('nav-topology').setAttribute('aria-current', list || usage ? 'false' : 'page');
  $('nav-incidents').setAttribute('aria-current', list ? 'page' : 'false');
  $('page-title').textContent = usage ? 'Agent Usage' : list ? '調查歷史與報告' : '調查工作台';
  $('page-description').textContent = usage ? '查看調查的 token 用量、Prompt cache 命中率與趨勢。' : list ? '查看每次調查的結論、工具證據與保存上下文。' : '追蹤服務觀測，並查看目前調查與 Monitor 紀錄。';
  let id = null;
  try { id = list && parts[1] ? decodeURIComponent(parts[1]) : null; error('route'); }
  catch { error('route', '調查網址編碼不合法。'); }
  const tab = parts[2] === 'chat' ? 'chat' : 'report';
  document.querySelectorAll('#incidents-view > :not(#incident-detail)').forEach(el => { el.hidden = Boolean(id); });
  if (id !== selectedHistory || tab !== selectedDetailTab) {
    selectedDetailTab = tab;
    selectedHistory = id; detailGeneration++; renderDetail();
    if (id) { void loadDetail(id); void loadEvents(id); }
  }
  if (!id) renderHistory();
  if (list && id) { $('page-title').textContent = tab === 'chat' ? '調查對話' : '調查報告'; $('page-description').textContent = '追溯取證過程，閱讀有依據的調查結果。'; }
  if (!list && !usage) renderGraph();
  if (usage) void loadUsage();
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
  await loadUsage();
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
  const receiveInvestigation = receive(value => {
    const fresh = store.acceptEvent(value, {stream: true});
    if (!fresh) return;
    renderCurrent(); renderUsagePage(); if (value.investigation_id === selectedHistory) renderDetail();
    if (['observation.recorded', 'tool.failed', 'report.submitted'].includes(value.type)) void loadDetail(value.investigation_id, value.investigation_id === selectedHistory);
    if (value.type === 'investigation.finished') {
      void loadDetail(value.investigation_id, value.investigation_id === selectedHistory); void loadHistory();
    }
    // Neither started nor finished changes active ID. Only state owns it.
  });
  events.addEventListener('investigation', receiveInvestigation);
  events.addEventListener('agent', receiveInvestigation);
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
  document.body.classList.add('chat-workspace');
  $('nav-usage').hidden = false;
  $('nav-incidents').href = '#investigations'; $('nav-incidents').innerHTML = icon('history') + '調查歷史 <span id="nav-count">—</span>';
  $('source').value = 'live'; $('source').onchange = event => { const url = new URL(location.href); url.searchParams.set('source', event.target.value); location.href = url.href; };
  $('investigation-phase').parentElement.querySelector('h2').innerHTML = icon('agent') + '調查對話';
  $('investigation-phase').insertAdjacentHTML('afterend', `<a class="chat-history-link" href="#investigations" title="查看調查歷史" aria-label="查看調查歷史">${icon('history')}</a>`);
  $('investigation-summary').before($('agent-usage'));
  $('show-journal').innerHTML = icon('chat') + '對話紀錄';
  $('show-report').innerHTML = icon('report') + '查看報告';
  $('journal-panel').insertAdjacentHTML('afterend', `<button id="chat-latest" class="chat-latest" hidden>${icon('down')}跳到最新訊息</button>`);
  $('chat-latest').onclick = () => { $('journal-panel').scrollTop = $('journal-panel').scrollHeight; $('chat-latest').hidden = true; };
  $('journal-panel').addEventListener('scroll', () => { if ($('journal-panel').scrollHeight - $('journal-panel').scrollTop - $('journal-panel').clientHeight < 70) $('chat-latest').hidden = true; });
  $('journal-entries').onclick = event => { const button = event.target.closest('[data-locate-node]'); if (button) locateNode(button.dataset.locateNode); };
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
  $('show-journal').onclick = () => { $('journal-panel').hidden = false; };
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
