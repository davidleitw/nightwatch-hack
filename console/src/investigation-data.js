import {validateGraph} from './data.js';

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const integer = value => Number.isSafeInteger(value) && value >= 0;
const statuses = ['running', 'completed', 'failed', 'interrupted'];
const types = ['investigation.started', 'tool.started', 'observation.recorded', 'tool.failed', 'investigation.finished'];

// Layout is a local view choice. It is not added to the backend graph payload.
export function graphLayout(graph) {
  return [...graph.nodes].sort((a, b) => a.id.localeCompare(b.id)).map((n, index) => ({
    id: n.id, layout: {row: Math.floor(index / 4), col: index % 4},
  }));
}
export function validateObservation(graph) {
  if (!object(graph) || !Array.isArray(graph.nodes) || graph.nodes.some(n => !object(n) || typeof n.id !== 'string')) throw new Error('graph 缺少合法節點。');
  validateGraph(graph, {nodes: graphLayout(graph)});
  if (!Number.isFinite(Date.parse(graph.at))) throw new Error('graph.at 不是合法時間。');
  return graph;
}
export function validateSummary(value) {
  if (!object(value) || typeof value.id !== 'string' || !value.id || !statuses.includes(value.status) || !integer(value.event_seq)) throw new Error('調查摘要缺少 id、status 或 event_seq。');
  return value;
}
export function validateInvestigationState(value) {
  if (!object(value) || value.schema_version !== 'nightwatch.investigation-state.v1' || !integer(value.cursor) || !Number.isFinite(Date.parse(value.server_now))) throw new Error('調查 state 格式不符。');
  if (!(value.active_investigation_id === null || typeof value.active_investigation_id === 'string') || !(value.last_completed_investigation_id === null || typeof value.last_completed_investigation_id === 'string')) throw new Error('調查 state 缺少 active 或 latest ID。');
  if (value.active_investigation_id !== null) {
    validateSummary(value.active_investigation);
    if (value.active_investigation.id !== value.active_investigation_id || value.active_investigation.status !== 'running') throw new Error('目前調查 ID 與摘要不一致。');
  } else if (value.active_investigation !== null) throw new Error('沒有 active ID 卻收到目前調查摘要。');
  if (value.graph !== null) validateObservation(value.graph);
  if (!(value.graph_error === null || typeof value.graph_error === 'string') || !(value.graph_received_at === null || Number.isFinite(Date.parse(value.graph_received_at)))) throw new Error('調查 state 缺少合法 graph_error 或 graph_received_at。');
  return value;
}
export function validateInvestigationEvent(value) {
  if (!object(value) || typeof value.investigation_id !== 'string' || !value.investigation_id || !integer(value.seq) || value.seq < 1 || !integer(value.cursor) || !types.includes(value.type) || !Number.isFinite(Date.parse(value.at)) || !object(value.payload)) throw new Error('調查事件格式不符。');
  if (['tool.started', 'observation.recorded', 'tool.failed'].includes(value.type) && (typeof value.payload.call_id !== 'string' || !value.payload.call_id)) throw new Error('工具事件缺少 payload.call_id，無法配對。');
  return value;
}
export async function requestJSON(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, {cache: 'no-store', ...options, signal: controller.signal,
      headers: {Accept: 'application/json', ...(options.body ? {'Content-Type': 'application/json'} : {}), ...options.headers}});
    const text = await response.text();
    let value;
    try { value = JSON.parse(text); } catch { throw new Error(`${path} 回傳非 JSON（HTTP ${response.status}）。`); }
    if (!response.ok) {
      const error = new Error(`${value.error?.code || 'http_error'}：${value.error?.message_zh || `HTTP ${response.status}`}`);
      error.status = response.status; error.details = value.error?.details; throw error;
    }
    return value;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error(`${path} 超過 10 秒未回應。`);
    throw error;
  } finally { clearTimeout(timer); }
}

export class InvestigationStore {
  state = null;
  stateWatermark = -1;
  eventCursor = null;
  events = new Map();

  acceptState(value, {initialStream = false} = {}) {
    validateInvestigationState(value);
    // Only a brand-new stream establishes the initial tail cursor. REST and
    // reconnect state must never skip history which is still being replayed.
    if (initialStream && this.eventCursor === null) this.eventCursor = value.cursor;
    if (value.cursor < this.stateWatermark || (value.cursor === this.stateWatermark && this.state && Date.parse(value.server_now) < Date.parse(this.state.server_now))) return false;
    this.stateWatermark = value.cursor; this.state = value; return true;
  }
  acceptEvent(value, {stream = false} = {}) {
    validateInvestigationEvent(value);
    let events = this.events.get(value.investigation_id);
    if (!events) { events = new Map(); this.events.set(value.investigation_id, events); }
    const fresh = !events.has(value.seq);
    if (fresh) events.set(value.seq, value);
    if (stream) this.eventCursor = Math.max(this.eventCursor ?? 0, value.cursor);
    return fresh;
  }
  activity(id) { return [...(this.events.get(id)?.values() || [])].sort((a, b) => a.seq - b.seq); }
}

export function pairedActivity(events, terminal = false) {
  const rows = [], calls = new Map();
  for (const event of events) {
    if (['tool.started', 'observation.recorded', 'tool.failed'].includes(event.type)) {
      const key = JSON.stringify([event.investigation_id, event.payload.call_id]);
      let row = calls.get(key);
      if (!row) { row = {kind: 'tool', investigation_id: event.investigation_id, call_id: event.payload.call_id, seq: event.seq}; calls.set(key, row); rows.push(row); }
      if (event.type === 'tool.started') row.started = event;
      else row.finished = event;
    } else rows.push({kind: 'event', event, seq: event.seq});
  }
  for (const row of calls.values()) row.status = row.finished ? (row.finished.type === 'tool.failed' ? 'failed' : 'recorded') : terminal ? 'incomplete' : 'running';
  return rows.sort((a, b) => a.seq - b.seq);
}
