# Persistent investigation sessions and frontend API

Status: implementation spec approved in scope by the user, 2026-09-12.
Implementation owner: Luna subagent. Review and frontend acceptance owner: parent agent.

## Goal and agreed behavior

- Run the existing Python agent loop from the existing FastAPI backend.
- Allow exactly one active investigation. No queue or concurrent investigations.
- Keep graph delivery running before, during and after investigations.
- Give every investigation its own context and durable ID. Preserve evidence,
  model-visible inputs, messages, usage, events and a readable terminal report.
- On completion, archive the session before clearing the active pointer. The main
  view clears investigation content/assessment overlays, not the live graph.
- Browser disconnects and refreshes neither cancel nor start investigations.
- All terminal outcomes, including insufficient evidence, errors, time budget
  exhaustion and backend restart, appear in history. Completed means investigation
  finished, never service repaired.
- New investigations start with fresh context. Do not inject old reports.

## Scope, ownership and compatibility

Edit only `control/`. Preserve all existing user changes. The parent commits each
completed, verified change as requested by the user; the subagent does not stage
or commit independently.
Do not edit `contracts/`, `console/`, `guardroom/`, root files or other projects.
Implement on `control/server/main.py`'s app; do not launch a second production API.
Do not change `/api/graph`'s handler, shape, dummy-data semantics or state selector.
Existing `/api/state`, `/events`, `/api/incidents/*`, fault/approval mock behavior
and their schemas remain compatible.

The earlier discussion's route names were provisional. Existing `state.v2` and
incident SSE are tied to legacy repair workflows and strict schemas. This feature
therefore owns `/api/investigations` and uses its own state/SSE. The frontend can
use this namespace for current investigations, reports and graph delivery without
depending on mock mode. Register typed schemas and English descriptions in the
same OpenAPI document. No rendering work in this task.

Allowed implementation changes: new session/store/runner/API modules under control,
small additive loop callbacks/identifiers, app registration, docs, gitignore entries
for local data and the explicitly listed tests below. No unrelated refactoring.
Dependency exception explicitly authorized by this spec: add the existing local
`nightwatch-agent` project as a dependency of `control/server` using uv's local path
source (`..`), and update the server lockfile as necessary. Reuse existing package
versions and available caches. SQLite is from the standard library. No new remote
third-party dependency or framework is needed.

Parent owns `INVESTIGATION-SPEC.md`, `INVESTIGATION-FRONTEND.md` and
`acceptance/investigation_api.py`. Subagent must not edit those files; report any
needed spec correction to the parent.

## Runtime and persistence

Use one FastAPI worker, with a background task owned by the application lifespan.
Use SQLite persistence, default `control/.data/investigations.sqlite3`, overridable
by `NIGHTWATCH_INVESTIGATION_DB`. Exclude DB files, journals and locks from git.
Enforce one active row transactionally (including simultaneous POSTs). Hold an
exclusive process lock on the database's adjacent lock file for the lifetime of
the manager; a second process must fail clearly rather than recover a live task.
The SQLite active constraint is still needed even with the process lock.

`NIGHTWATCH_GRAPH_URL` selects the source URL, default
`http://127.0.0.1:8001/api/graph`. It is operator configuration, never POST input.
Use the existing GraphAPI projection and actual investigate() function. Do not
mix the old recording with new graph evidence. The real runner uses the existing
model environment configuration and English prompts. No automatic model call on
startup, SSE connection or GET. No production fake-model switch or hard-coded
conclusion. Missing model configuration produces a durable failed investigation
with an explicit report. Dependency injection for offline tests is allowed.

At creation persist ID, request id/body, creation order, created/started times,
trigger, running status and the started event. Persist context before the model
request, and tool events/evidence as they occur. On completion preserve serialized
framework messages, prompt text, exposed tool schemas/descriptions, opening input,
model name/settings without credentials, evidence and usage. Do not just save a
provider response ID. Initial context plus recorded tool events must survive a
crash; incomplete transcripts must be labeled partial.

Finish in one transaction: terminal status + report + final context/evidence +
closed_at + durable investigation.finished event + release active slot. Publish
only committed events. Guard writes by session ID so an old task cannot clear a
new session. If final storage fails, do not announce completion or release the slot.

On orderly shutdown interrupt the active task and persist a readable interrupted
report. On restart mark any still-running session interrupted, append its terminal
event, clear active and retain its partial evidence. Do not automatically resume.

## Session and report representations

Session summary fields (required unless stated):

```text
id: string
created_seq: integer             # stable ordering for list pagination
status: running | completed | failed | interrupted
outcome: null | report_ready | unresolved | budget_exhausted | execution_failed | interrupted
created_at: UTC RFC3339 string
started_at: UTC RFC3339 string
closed_at: UTC RFC3339 string | null
trigger: {source: manual | detector, reason: string}
summary_zh: string
event_seq: integer              # latest per-session event sequence
```

`completed` covers normal LoopResult exits, including unresolved/budget_exhausted.
Exceptions before/during running give failed/execution_failed. System interruption
gives interrupted/interrupted. Do not invent an accepted root-cause report from a
graph. Preserve the current loop's evidence validation requirements.

Session detail adds `report`, `evidence`, `usage`, `context_available`,
`context_complete`. Report is null while running; terminal report is always:

```text
investigation_id, outcome, summary_zh, started_at, closed_at,
agent_report: object | null, evidence_ids: string[], limitations: string[]
```

Use actual model/reason/error text for summary, never fault-card truth. For graph
mode disclose the synthetic source and lack of history/log/trace data. For failures
retain any already-recorded evidence. Do not return secrets or provider auth objects.

## HTTP API

All new endpoints work independently of NIGHTWATCH_MOCK_DATA. API error envelope:
`{"error":{"code":"...","message_zh":"...","details":{...}}}`.
Use no-store responses. Validate unknown fields, empty IDs, duplicate/unknown query
parameters and pagination bounds. Return consistent 400 for invalid input.

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/api/investigations` | Body `{request_id, trigger?: {source, reason}}`; default trigger manual/user requested. Return 202 `{investigation_id, status:"running"}` immediately after durable creation, not after model execution. |
| GET | `/api/investigations/state` | Return state described below, even if idle or graph unavailable. |
| GET | `/api/investigations/stream` | SSE described below, available while idle and without a model key. |
| GET | `/api/investigations?limit=20&before=N` | Newest first, 1..100 limit, `before` exclusive created_seq. Return `{items:[summaries], next_before: integer|null}`. Include running and all terminal outcomes. |
| GET | `/api/investigations/{id}` | Detail including terminal report and evidence. Unknown ID gives 404. |
| GET | `/api/investigations/{id}/events?after=0&limit=100` | Ordered per-session events with seq > after, limit 1..500. Return `{items:[events], next_after: integer|null}`. Cursor is exclusive and returned only if more events exist. |
| GET | `/api/investigations/{id}/context` | Persisted context snapshot; return `{complete:bool, context:object}`. Explicitly mark partial while running/interrupted. |

Same request_id and normalized body returns the original 202 identity even after
completion; do not run again. Reusing request_id with different content gives 409
`request_conflict`. A different request while active gives 409
`investigation_active`, `details.active_investigation_id`. Check replay before busy.
The detector can invoke the same manager create method, not bypass persistence or
one-at-a-time admission. Do not implement an automatic detector in this task.

State shape:

```text
schema_version: "nightwatch.investigation-state.v1"
server_now: UTC RFC3339 string
cursor: integer                 # highest committed global investigation cursor
active_investigation_id: string | null
active_investigation: summary | null
last_completed_investigation_id: string | null  # latest terminal session of any outcome
graph: public snapshot-shaped object | null
graph_received_at: UTC RFC3339 string | null
graph_error: string | null
```

State active pointer, active summary and cursor must come from a consistent DB
read. Graph is independently refreshed. Do not reset node health on completion.
New state/stream graph projections set `assessment` to `unassessed` to exclude
prefilled demo conclusions; they retain all observational fields and schema shape.
Agent annotations belong to the active session, never persisted into source graph.

## Events and SSE synchronization

Every persisted event has:

```text
cursor: integer                 # global durable autoincrement, never reset on normal restart
investigation_id: string
seq: integer                    # monotonic per session, starts at 1
type: investigation.started | tool.started | observation.recorded | tool.failed | investigation.finished
at: UTC RFC3339 string
payload: object
```

Tool events have a stable per-call `call_id` in payload, the same across started
and recorded/failed. Retain exact tool args/result and evidence ID. Existing loop
events currently lack call_id; add it compatibly or pair them unambiguously in
the adapter. No success/failure pairing by tool name alone. Prevent duplicate
terminal events when wrapping the loop's investigation.finished callback.
Persist event/evidence before publishing it. This task does not expose raw private
reasoning or token-by-token output; tool activity and final explanations suffice.

Stream behavior:

- `event: state` carries the state projection and its cursor. Send immediately on
  connection and after lifecycle changes. State alone controls the active pointer.
- `event: investigation` carries a persisted event, with `id: <cursor>`.
- `event: graph` carries the public snapshot projection; no SSE id. Deliver at
  least every 5 seconds while the source is reachable, including while idle and
  after a session finishes. Do not fabricate advancing seq/at for the dummy source.
- `event: ping` carries server_now every 2 seconds; no SSE id.
- Graph failures leave graph_error visible in state and keep SSE alive; an old
  cached graph retains its original at/seq and last successful graph_received_at.
- With no reconnect cursor: capture state/high-watermark H consistently, send it,
  then tail investigation events > H. Historical events are fetched via REST.
- Reconnect accepts `?after=N` or Last-Event-ID. Both must be nonnegative integers;
  if both supplied they must match. Future cursors and malformed IDs give 400
  before streaming headers. Send current state first, then replay all events > N
  in ascending cursor order and continue tailing without gaps/duplicates.
- Replayed events can be older than the initial state cursor. They populate
  history but must not roll the active session backwards. Document this explicitly.
- Do not consume events destructively: two connected browsers see the same stream.
- A slow/disconnected browser must not block the worker. Database-backed polling
  with bounded batches is sufficient; never fetch an unbounded journal on each tick.
- Graceful app shutdown must finish streaming tasks and close resources.

Frontend contract: always render graph independently; track active ID only from
state; store events keyed by (investigation_id, seq) and ignore duplicates; associate
tool rows by call_id; never clear a new session because an old finished event arrived.
When active becomes null, clear only the previous investigation view/overlays;
show report via the persisted history/detail endpoints. Report exists before the
finished event is visible. Restart and refresh must reconstruct equivalent state.

## Explicit tests and acceptance

The task explicitly requires these tests under control (stdlib unittest and existing
test dependencies only). Test substitutions must be labeled; never claim model
reasoning verified by FunctionModel.

1. Two simultaneous creates admit one; request-id replay/conflict persist across
   manager restart; busy response includes the active ID.
2. Execute actual investigate() using FunctionModel and the HTTP graph API; capture
   tool started/result pairing and sanitized snapshot evidence; unresolved still
   produces a saved readable report and frees the active slot.
3. Next investigation has independent context/evidence/call IDs and cannot be
   affected by previous completion callbacks.
4. Completion event implies detail/report are already durable and current state
   is consistent. Report, context and exact observed snapshots survive restart.
5. Active sessions interrupted by restart retain partial events/evidence and gain
   one terminal report/event. No permanent busy state after recovery.
6. SSE initial state, two subscribers, Last-Event-ID/query replay, invalid/future
   cursors, per-session event pagination and stable report-list pagination.
7. Graph delivery and ping continue idle/running/finished; graph health/seq/at never
   reset on finish; source failure remains visible without terminating the stream.
8. Real loop/configuration failures produce failed or unresolved report explicitly;
   no secret leakage or recording fallback. Browser disconnection does not cancel.
9. Existing 11 loop tests still pass. Existing graph and legacy mock endpoints
   retain shapes and behavior. OpenAPI includes the new typed endpoints/schemas.
10. Offline package build/install from existing local projects; start real HTTP
    service on a temporary port for frontend acceptance, stop every test process.

Provide precise commands and observed outputs in the implementation handoff.
Do not commit, push or open a PR. Parent will independently exercise the frontend
API and review lifecycle/storage behavior before reporting completion.
