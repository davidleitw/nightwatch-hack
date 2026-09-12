# Connecting the frontend to investigation sessions

This document defines the frontend-facing behavior implemented under
`/api/investigations`. The API lives on the existing graph server. The full backend
requirements and field definitions are in [INVESTIGATION-SPEC.md](INVESTIGATION-SPEC.md).
No frontend rendering changes are included in this backend task.

## Initial connection and refresh

Read `GET /api/investigations/state` or open
`GET /api/investigations/stream`, whose first event is `state`.

The state contains the latest available `graph`, its receive time/error, the
active investigation ID and summary, the latest terminal investigation ID and
a durable investigation event `cursor`. Graph can be null while the provider is
unavailable; this does not prevent investigation history from loading.

Subscribe to the new stream once. It delivers four event names:

| SSE event | Contents | Frontend responsibility |
| --- | --- | --- |
| `state` | Current state with active ID and cursor | Reconcile the active investigation; this event owns the active pointer |
| `graph` | Current observation graph | Update graph independently of investigation state |
| `investigation` | Persisted event with investigation_id, seq, cursor, type, at and payload | Append/deduplicate the corresponding investigation's activity |
| `ping` | server_now | Track transport connectivity, not observation freshness |

Only `investigation` events have SSE `id:`. Graph sequence/time belongs to the
observation source and may remain unchanged in the current dummy API. Receiving
a ping or another copy of a graph does not prove new measurements arrived.

## Start one investigation

```http
POST /api/investigations
Content-Type: application/json

{"request_id":"<unique user action ID>","trigger":{"source":"manual","reason":"Inspect the current graph"}}
```

A successful request returns `202` with `investigation_id`. The server continues
in the background; neither this HTTP response nor a browser connection owns the
worker. Reuse the same request_id when retrying an uncertain submission. The
same normalized request returns the original ID even after completion.

A different request during an active session returns `409 investigation_active`
and `error.details.active_investigation_id`. Reusing a request_id with changed
content returns `409 request_conflict`. Do not silently issue a new request ID
to bypass either response.

The frontend does not choose the graph URL, model credentials or a dummy scenario
through this endpoint. These are backend configuration.

## Activity, graph and completion

Keep the graph and investigation activity in separate state:

- Graph updates continue while the investigation view is empty, running or finished.
- Store activity by `(investigation_id, seq)`. Pair each tool start with its result
  or failure using `(investigation_id, payload.call_id)`.
- An `investigation.finished` event says a report is already saved. It does not
  claim that the service recovered, and does not authorize resetting health colors.
- Use `state.active_investigation_id` to decide which investigation belongs in the
  main view. When it becomes null, clear investigation activity and assessment
  overlays from that view. Keep archived data and the current graph.
- If another investigation has already started, a delayed finished event for the
  old ID must not clear it. Never clear the whole view solely because a finished
  event arrived.

The new stream/state graph uses `assessment: unassessed` for observational nodes;
its health, source freshness, source seq and source timestamp stay intact. Agent
conclusions can be shown as a separate active-investigation overlay. Clear that
overlay when the active ID changes, without changing the underlying observations.

## Replay without rolling the main view backwards

Reconnect with `Last-Event-ID: <cursor>` or `?after=<cursor>`. If both are supplied
they must match. Cursor is the global event cursor, not the per-session seq and
not the graph seq. Malformed or future cursors return 400.

The server first sends the current state, then replays persisted events after the
requested cursor. Replayed events can predate that state's cursor. They fill
history; they must not undo the current active-session selection.

The following is illustrative state handling, independent of UI libraries:

```javascript
let activeId = null;
let stateCursor = -1;
let lastReceivedEventCursor = null;
const eventsByInvestigation = new Map();

function onState(state) {
  if (state.cursor < stateCursor) return;
  stateCursor = state.cursor;
  activeId = state.active_investigation_id;
  // Select activity only from eventsByInvestigation.get(activeId).
  // Reconcile graph using its observation metadata, independently of activeId.
}

function onInvestigation(event) {
  let events = eventsByInvestigation.get(event.investigation_id);
  if (!events) {
    events = new Map();
    eventsByInvestigation.set(event.investigation_id, events);
  }
  events.set(event.seq, event);
  lastReceivedEventCursor = Math.max(lastReceivedEventCursor ?? 0, event.cursor);
  // Do not assign or clear activeId here, including for old finished events.
}
```

Keep the current-state watermark separate from the last received event cursor:
a fresh state can be ahead of replay still in progress. Promoting the reconnect
cursor to the state's watermark too early would skip unread history on disconnect.
On a brand-new connection with no requested replay, its initial state watermark
is the starting cursor. On reconnect, retain the actual requested/received cursor
until replay catches up.

If the browser did not keep the current session's earlier activity, fetch
`GET /api/investigations/{id}/events?after=0&limit=100`. Merge it by session and seq
while continuing to receive the stream. A newer state remains authoritative.

## History, reports and saved context

`GET /api/investigations?limit=20` returns newest-first summaries and `next_before`.
Pass that value as `before` for the next page. Include all outcomes in the report
browser, including failed and interrupted sessions.

`GET /api/investigations/{id}` returns the current detail, evidence, usage and
`report`. Running sessions have report=null. Every terminal session has a readable
report, even when `agent_report` is null because no root cause was established.

`GET /api/investigations/{id}/context` returns saved instructions, tools, opening,
model settings and framework messages. `complete:false` means the transcript is
partial; it must not be presented as a complete model conversation. Contexts are
independent and are not automatically copied into new investigations.

Two additional read endpoints are available:

- `GET /api/investigations/{id}/report` returns exactly the saved terminal report.
  Running sessions return 409 `investigation_active`; unknown IDs return 404.
- `GET /api/investigations/{id}/snapshots` returns
  `{investigation_id, snapshots:[{evidence_id, snapshot}]}` in evidence order.
  These are exact saved `get_graph` observations, including repeated queries.
  Empty means no successful graph observation was saved; it does not prove that
  the monitored service was healthy. Snapshots remain readable after ring expiry.

Session lifecycle and conclusion are separate:

| Status | Meaning |
| --- | --- |
| running | The backend owns an active investigation |
| completed | The loop ended normally; outcome may be report_ready, unresolved or budget_exhausted |
| failed | Execution/configuration failed; a report describes the failure |
| interrupted | Shutdown/restart interrupted execution; partial context and evidence remain |

The current graph-only agent has no connected history/log/trace tools. Its existing
root-cause validator therefore cannot accept a root-cause report from one graph.
An unresolved report is a valid archived investigation, not a missing report.

## Coexistence with existing APIs

`/api/graph` retains its monitor behavior. `/api/state`, `/events` and legacy
incident read APIs now project actual monitor observations and saved investigations
without mock mode; see [FRONTEND-API.md](FRONTEND-API.md). Legacy incident lifecycle
is a limited compatibility projection: completed investigations never imply recovered
services. The original status/outcome, context and full saved report remain in this
namespace. A complete legacy experiment report still requires injection truth,
baseline, audit and verification data that are not connected.

The console workspace on master (#19) now consumes the native investigation API,
including creation, history and saved reports. See
[console integration](../console/GUARDROOM-INTEGRATION.md) for its connection contract.
Browser end-to-end acceptance against this backend remains outstanding.
