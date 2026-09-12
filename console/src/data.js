const phases = new Set(['baseline','detected','investigating','awaiting_approval','executing','verifying','recovered','unresolved','closed']);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
let localMock = false;
export function setMockMode(enabled) { localMock = enabled; }
export function apiPath(path) {
  if (!localMock) return path;
  const url = new URL(path, location.origin);
  url.searchParams.set('scenario', new URLSearchParams(location.search).get('scenario') || 'cycle');
  return url.pathname + url.search;
}
export function validateIncident(value, detail = false) {
  if (!object(value) || typeof value.id !== 'string' || !value.id || !phases.has(value.phase) || typeof value.detected_at !== 'string' || !Number.isFinite(Date.parse(value.detected_at))) throw new Error('事故資料格式不符：需要 id、phase 與 detected_at。');
  if (detail && value.nodes !== undefined && !Array.isArray(value.nodes)) throw new Error('事故 nodes 不是陣列。');
  return value;
}
export function validateGraph(graph, capabilities) {
  if (!object(graph) || graph.schema_version !== 'nightwatch.snapshot.v2' || !Number.isInteger(graph.seq) || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges) || !object(graph.sources)) throw new Error('服務快照格式不符。');
  const ids = new Set();
  for (const n of graph.nodes) {
    if (!n || typeof n.id !== 'string' || !n.id || ids.has(n.id)) throw new Error('服務節點 ID 缺少或重複。');
    if (!['ok','warning','failing','unknown'].includes(n.status) || !['unassessed','suspect','ruled_out','origin'].includes(n.assessment)) throw new Error(`節點 ${n.id} 的健康或判定不合法。`);
    for (const key of ['traffic','errors','p95_ms','saturation']) if (n[key] !== null && (typeof n[key] !== 'number' || !Number.isFinite(n[key]))) throw new Error(`節點 ${n.id} 缺少合法的 ${key}。`);
    ids.add(n.id);
  }
  if (!Array.isArray(capabilities?.nodes)) throw new Error('缺少 capabilities.nodes，無法依契約定位服務。');
  const layoutIds = new Set();
  const cells = new Set();
  for (const n of capabilities.nodes) {
    if (!n || !ids.has(n.id) || layoutIds.has(n.id) || !Number.isInteger(n.layout?.row) || !Number.isInteger(n.layout?.col)) throw new Error('節點位置或 capabilities.nodes 與 graph.nodes 名單不一致。');
    const cell = `${n.layout.row}:${n.layout.col}`;
    if (cells.has(cell)) throw new Error('多個服務共用同一個 layout 位置，無法不重疊地顯示。');
    cells.add(cell); layoutIds.add(n.id);
  }
  if (layoutIds.size !== ids.size) throw new Error('capabilities.nodes 與 graph.nodes 數量不一致。');
  for (const edge of graph.edges) if (!ids.has(edge.from) || !ids.has(edge.to) || !['calls','uses','publishes','consumes'].includes(edge.kind) || typeof edge.observed !== 'boolean') throw new Error('服務連線端點或種類不符合契約。');
  return graph;
}
export function validateState(state) {
  if (!object(state) || state.schema_version !== 'nightwatch.state.v2' || typeof state.run?.id !== 'string') throw new Error('不是合法的 nightwatch.state.v2 資料。');
  validateGraph(state.graph_now, state.capabilities);
  if (state.incident !== null) validateIncident(state.incident, true);
  return state;
}
export async function readJSON(path) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(path.startsWith('/api/') ? apiPath(path) : path, {signal:controller.signal, cache:'no-store', headers:{Accept:'application/json'}});
    const text = await response.text();
    let data;
    try { data = JSON.parse(text); } catch { throw new Error(`${path} 回傳非 JSON 資料（HTTP ${response.status}）。`); }
    if (!response.ok) { const error = new Error(`${data.error?.code || 'http_error'}：${data.error?.message_zh || `HTTP ${response.status}`}`); error.status=response.status; throw error; }
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error(`${path} 超過 8 秒未回應。`);
    throw error;
  } finally { clearTimeout(timer); }
}
async function readLines(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`讀取錄影 ${path} 失敗（HTTP ${response.status}）。`);
  return (await response.text()).split('\n').filter(line => line.trim()).map(line => JSON.parse(line));
}
export async function loadRecording() {
  const [initial, final, snapshots, events] = await Promise.all([readJSON('recordings/state.initial.json'),readJSON('recordings/state.json'),readLines('recordings/snapshots.jsonl'),readLines('recordings/events.jsonl')]);
  return {initial,final,snapshots,events};
}
export function projectRecording(recording, seconds) {
  const at = Date.parse(recording.final.incident.detected_at) + seconds * 1000;
  const state = structuredClone(recording.initial);
  let incident = null;
  for (const line of recording.events) {
    if (line.event !== 'incident') continue;
    const commit = line.data, event = commit.event;
    if (typeof event.t === 'number' ? event.t > seconds : Date.parse(event.occurred_at) > at) continue;
    if (!commit.incident_id) continue;
    if (event.type === 'incident.detected') incident = {id:commit.incident_id,run_id:commit.run_id,phase:'detected',outcome:null,detected_at:event.occurred_at,closed_at:null,card_id:'',detection:event.payload.detection,nodes:[],evidence:[],revision:commit.revision};
    if (!incident) continue;
    if (event.incident_transition) incident.phase=event.incident_transition.to;
    Object.assign(incident,commit.incident || {});
    for (const key of ['proposal','approval','execution','verification','audit','hypothesis']) if (commit[key]) incident[key]=structuredClone(commit[key]);
    if (commit.new_evidence) incident.evidence.push(...structuredClone(commit.new_evidence));
    if (event.type==='hypothesis.concluded' && event.payload.hypothesis) incident.hypothesis=structuredClone(event.payload.hypothesis);
    if (event.type==='incident.completed') Object.assign(incident,{closed_at:event.occurred_at,card_id:event.payload.card_id || '',outcome:event.payload.outcome});
    incident.revision=commit.revision;
  }
  const graph = recording.snapshots.filter(s=>typeof s.t === 'number' ? s.t <= seconds : Date.parse(s.at)<=at).at(-1);
  if (!graph) throw new Error('錄影在此時間沒有服務快照。');
  state.graph_now=structuredClone(graph); state.server_now=new Date(at).toISOString(); state.incident=incident;
  if (incident) incident.nodes=graph.nodes.map(({id,status,assessment})=>({id,status,assessment}));
  state.next_step_zh=incident?.phase==='awaiting_approval'?'等待操作者批准':incident?.phase==='recovered'?'事故已修復':'依錄影時間查看服務與事故';
  return validateState(state);
}
