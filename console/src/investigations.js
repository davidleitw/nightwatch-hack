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
  $('start-investigation').textContent = posting ? '正在送出…' : pendingRequest ? '重試同一次調查' : '開始調查';
}
function acceptGraph(value, receivedAt = null) {
  validateObservation(value);
  // Avoid a slow state GET rolling an independently delivered graph backwards.
  if (graph && Date.parse(value.at) < Date.parse(graph.at)) return;
  if (graph && value.at === graph.at && value.seq < graph.seq) return;
  graph = value; graphReceivedAt = receivedAt; graphGeneration++;
  if (!graph.nodes.some(n => n.id === selectedNode)) selectedNode = graph.nodes[0]?.id ?? null;
  renderGraph();
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
}

function renderGraph() {
  $('active-phase').textContent = activeId() ? '調查中' : store.state ? '無進行中調查' : '等待狀態';
  if (!graph) {
    $('graph-empty').hidden = false; $('graph-empty').textContent = '尚未取得服務拓樸。';
    return;
  }
  const layout = graphLayout(graph), signature = JSON.stringify(layout.map(n => n.id));
  if (signature !== layoutSignature) { layoutSignature = signature; fit = true; }
  const width = Math.min(4, Math.max(1, graph.nodes.length)) * 160 + 24;
  const height = Math.max(1, Math.ceil(graph.nodes.length / 4)) * 150 + 24;
  if (fit) zoom = Math.max(.5, Math.min(1.15, ($('graph-viewport').clientWidth - 16) / width));
  const positions = new Map(layout.map(n => [n.id, {x: 20 + n.layout.col * 160, y: 20 + n.layout.row * 150}]));
  const search = $('node-search').value.toLowerCase().trim(), selectedHealth = $('health-filter').value;
  const matches = new Set(graph.nodes.filter(n => (!search || n.id.toLowerCase().includes(search)) && (selectedHealth === 'all' || n.status === selectedHealth)).map(n => n.id));
  $('total-nodes').textContent = graph.nodes.length;
  $('failing-count').textContent = graph.nodes.filter(n => n.status === 'failing').length;
  $('warning-count').textContent = graph.nodes.filter(n => n.status === 'warning').length;
  $('snapshot-at').textContent = at(graph.at);
  $('graph-count').textContent = `${graph.nodes.length} 個節點 / ${graph.edges.length} 條連線`;
  $('match-count').textContent = `${matches.size} / ${graph.nodes.length} 個節點符合條件`;
  $('graph-empty').hidden = graph.nodes.length > 0;
  $('graph-empty').textContent = '後端快照目前沒有服務節點。';
  $('graph-canvas').style.width = `${width * zoom}px`; $('graph-canvas').style.height = `${height * zoom}px`;
  for (const id of ['nodes', 'edges']) { $(id).style.width = `${width}px`; $(id).style.height = `${height}px`; $(id).style.transform = `scale(${zoom})`; }
  $('edges').setAttribute('width', width); $('edges').setAttribute('height', height);
  $('zoom-value').textContent = `${Math.round(zoom * 100)}%`;
  $('edges').innerHTML = '<defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0L7 3.5L0 7" fill="none" stroke="#72856c"/></marker></defs>' + graph.edges.map((e, index) => {
    const a = positions.get(e.from), b = positions.get(e.to);
    const lane = a.y === b.y ? a.y + 122 + index % 3 * 5 : Math.min(a.y, b.y) + 125 + index % 3 * 5;
    const d = a.y === b.y && Math.abs(a.x - b.x) === 160 ? `M${a.x + (a.x < b.x ? 120 : 0)} ${a.y + 53}H${b.x + (a.x < b.x ? 0 : 120)}` : `M${a.x + 60} ${a.y + 106}V${lane}H${b.x + 60}V${b.y}`;
    return `<path class="edge ${e.observed ? '' : 'unobserved'} ${e.from === selectedNode || e.to === selectedNode ? 'related' : ''}" d="${d}" marker-end="url(#arrow)"><title>${esc(e.from)} → ${esc(e.to)} · ${esc(e.kind)} · ${e.observed ? '已觀測' : '近期未觀測'}</title></path>`;
  }).join('');
  $('nodes').innerHTML = graph.nodes.map(n => {
    const p = positions.get(n.id);
    const values = {traffic: number(n.traffic, ' req/s'), errors: number(n.errors, '%', 100), latency: number(n.p95_ms, ' ms'), saturation: number(n.saturation, '%', 100), liveness: n.alive === true ? 'alive: true' : n.alive === false ? 'alive: false' : '—'};
    return `<button class="graph-node ${esc(n.kind)} ${selectedNode === n.id ? 'selected' : ''} ${matches.has(n.id) ? '' : 'dim'}" data-node="${esc(n.id)}" style="left:${p.x}px;top:${p.y}px" aria-pressed="${selectedNode === n.id}"><span class="node-name">${esc(n.id)}</span><span class="node-meta ${esc(n.status)}"><span><i class="dot ${esc(n.status)}"></i>${esc(health[n.status])}</span><span>觀測</span></span><span class="node-value">${esc(values[n.primary_axis] ?? '主要量測 —')}</span></button>`;
  }).join('');
  $('nodes').querySelectorAll('[data-node]').forEach(button => button.onclick = () => locateNode(button.dataset.node));
  $('sources').innerHTML = '<span>觀測來源</span>' + ['prometheus', 'jaeger', 'logstore'].map(key => {
    const s = graph.sources[key];
    return `<span>${key} · ${s?.ok === true ? '可用' : s?.ok === false ? '不可用' : '無資料'} · ${esc(number(s?.age_secs, ' 秒前'))}</span>`;
  }).join('');
  $('footer-status').textContent = `來源快照 #${graph.seq} · 觀測時間 ${at(graph.at)} · 後端接收 ${at(graphReceivedAt)}；心跳不代表新量測`;
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
  if (!n) { $('node-detail').innerHTML = '<div class="empty">點選節點查看後端量測。</div>'; return; }
  const metrics = [['請求量', number(n.traffic, ' req/s')], ['錯誤率', number(n.errors, '%', 100)], ['P95', number(n.p95_ms, ' ms')], ['飽和度', number(n.saturation, '%', 100)], ['alive', n.alive === true ? 'true' : n.alive === false ? 'false' : '—']];
  $('node-detail').innerHTML = `<div class="node-detail-top"><h3>${esc(n.id)}</h3><span class="${esc(n.status)}">${esc(health[n.status])}</span></div><div class="node-metrics">${metrics.map(([label, value]) => `<div><span class="metric-label">${label}</span><strong>${esc(value)}</strong></div>`).join('')}</div><p class="report-note">alive 與來源量測的定義由後端決定；調查結束不會改變服務健康。</p><details><summary>原始節點與相鄰連線</summary>${raw({node: n, edges: graph.edges.filter(e => e.from === n.id || e.to === n.id)})}</details>`;
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
  $('topology-view').hidden = list; $('incidents-view').hidden = !list;
  $('nav-topology').setAttribute('aria-current', list ? 'false' : 'page');
  $('nav-incidents').setAttribute('aria-current', list ? 'page' : 'false');
  $('page-title').textContent = list ? '調查歷史與報告' : '調查工作台';
  $('page-description').textContent = list ? '查看每次調查的結論、工具證據與保存上下文。' : '追蹤服務觀測，並查看目前調查與 Monitor 紀錄。';
  let id = null;
  try { id = list && parts[1] ? decodeURIComponent(parts[1]) : null; error('route'); }
  catch { error('route', '調查網址編碼不合法。'); }
  if (id !== selectedHistory) {
    selectedHistory = id; detailGeneration++; renderDetail();
    if (id) { void loadDetail(id); void loadEvents(id); }
  }
  renderHistory(); if (!list) renderGraph();
}

async function refresh() {
  if (refreshBusy) return;
  refreshBusy = true; $('refresh').disabled = true;
  const generation = graphGeneration;
  try {
    const value = await requestJSON('/api/investigations/state');
    acceptState(value, {acceptGraph: generation === graphGeneration});
  } catch (e) { error('state', `調查狀態讀取失敗：${e.message}`); }
  finally { refreshBusy = false; $('refresh').disabled = false; }
  await loadHistory();
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
  document.removeEventListener('visibilitychange', visibilityChanged);
}

export function startWorkspace() {
  document.title = 'NightWatch · 調查工作台';
  $('nav-incidents').href = '#investigations'; $('nav-incidents').innerHTML = '調查歷史 <span id="nav-count">—</span>';
  $('source').value = 'live'; $('source').onchange = event => { const url = new URL(location.href); url.searchParams.set('source', event.target.value); location.href = url.href; };
  $('investigation-phase').parentElement.querySelector('h2').textContent = '目前調查';
  $('show-report').textContent = '目前調查報告';
  $('active-phase').previousElementSibling.textContent = '目前調查';
  $('graph-viewport').parentElement.querySelector('.assessment-legend').textContent = '健康依後端觀測；按節點 ID 排列，位置不表示呼叫順序。';
  $('page-title').closest('.page-heading').insertAdjacentHTML('afterend', '<div class="investigation-actions"><button id="start-investigation" disabled>開始調查</button><span id="submission-status" role="status">僅在按下按鈕後開始；重新整理不會建立調查。</span></div>');
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
