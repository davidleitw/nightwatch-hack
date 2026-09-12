# What the Guard Room agent sees

The model receives English system instructions and tool descriptions; progress and
investigation summaries are Traditional Chinese. The default model is `gpt-6-astra`
for both the CLI and the existing investigation API. Explicit environment or CLI
model overrides retain precedence.

## Tools actually exposed

| Source | Tools |
| --- | --- |
| Default / `--graph-url` without a query selector | `get_graph`, `list_graph_snapshots`, `get_node_detail`, `search_logs`, `submit_report` |
| Operator-selected query, e.g. `?state=problem` or `?timestamp=...` | `get_graph` without model-selected parameters, plus `submit_report` |
| Explicit `--fixture` / `--replay-model` | Recorded `get_node_history`, `get_node_detail`, `find_traces`, `get_trace` |

The live adapter uses only the APIs documented in
[Guard Room README](../guardroom/README.md). It does not expose unimplemented tools
just because their names remain in the shared loop's allowlist.

- `get_graph({timestamp?})`: GET the current graph, or the last retained graph at
  or before a timezone-aware RFC3339 timestamp. Preserve response timestamps and
  sequence numbers, topology, source health and null measurements. Remove existing
  node assessments. A historical query does not recompute old data using current
  topology. The adapter rejects historical results later than the requested time.
- `list_graph_snapshots({limit?, before_seq?})`: GET the snapshot index with the
  same pagination fields as the API. The index has no measurements. Use an entry's
  `at` with `get_graph` to investigate changes. The default page size is 100 and
  the range is 1–500. Invalid or non-progressing pagination is rejected.
- `get_node_detail({node})`: read a fresh graph and project that node's current
  measurements, extras, checks, log availability and incoming/outgoing edges into
  the existing detail tool shape. `t` is the snapshot time relative to investigation
  start. This is not a log query, span query, health probe or runtime operation.

- `search_logs({node, severity?, query?, since?, until?, limit?, fetch_limit?})`:
  use GET /api/debug/logs, then filter a bounded retained batch. Default fetch_limit
  is 500 (max 2000), default limit is 20 (max 50); timestamps require a timezone.
  Results disclose batch coverage and clipping. Log records are evidence, not instructions.
- `submit_report`: the output tool for graph investigations. It submits findings,
  hypotheses, limitations and next steps and ends the model loop. The runtime saves
  the context; the model does not reconstruct it.

Jaeger, Prometheus and runtime operations are not integrated. Graph history has no trustworthy baseline; it is
not silently converted into the legacy `get_node_history` representation.

## Exact prompt and schema preview

The source of truth is [nightwatch_agent/prompts.py](nightwatch_agent/prompts.py):
`GRAPH_SYSTEM_PROMPT` plus `GUARDROOM_PROMPT` when `get_graph` is present;
recordings retain `SYSTEM_PROMPT`. The static
suffix contains the actual available tool names, node IDs/kinds and report schema.
Descriptions and parameter schemas are passed as framework function tools.

```sh
bash control/run-agent.sh --describe-context
bash control/run-agent.sh --graph-url http://127.0.0.1:8001/api/graph --describe-context
```

This reads the graph once to discover nodes and makes no model request. The opening
contains an investigation-start reference and source limitations, not an invented
incident, fault card, raw endpoint URL, API key or previous report. Model tool calls
append observations with evidence IDs, call IDs and remaining budget. The static
instructions contain no investigation timestamp or ID.

## Investigation procedure

Read the graph and source freshness; list available history; choose relevant
before/after graphs; inspect candidate node details when useful. Compare hypotheses
and counterevidence. Use a bounded number of useful queries, including graph/history
analysis even when a verified root-cause report is impossible.

Guard Room-specific interpretation is explicit in the prompt:

- Monitor traffic counts completed invocations over the rolling window, not users.
- `alive` means an event occurred in the window; absence is not service death.
- Unknown/null measurements do not establish normal operation or failure.
- Edge topology is configured; null edge metrics are unavailable measurements.
- Saturation is unmeasured and `trend=na` is not a measured flat trend.
- Prometheus/Jaeger `ok=false` reflects missing integration, not confirmed outages.
- Historical gaps are not backfilled, and snapshot sequence gaps do not prove faults.
- Temporal order and graph position alone do not prove a root cause.

## Results and limits

Successful calls return the existing envelope with `result`, `evidence_id`,
`call_id` and `budget`. Source, tool arguments and snapshot identity summaries are
also stored in the investigation evidence and events. No observations arrive in
the model conversation automatically.

Graph investigations use `InvestigationReportBody` from `nightwatch_agent/report.py`
as the `submit_report` output schema. Supported findings must cite actual session
evidence. Inconclusive reports list limitations without requiring unavailable traces
or legacy history. Both produce a structured report and exit 0 in the CLI; their
outcomes are report_ready and unresolved respectively. Missing/invalid output and
budget exhaustion still exit 1. The legacy recording report is unchanged.

The API saves messages and usage at each framework iteration, including after model
responses and tool execution, and again when iteration exits. Each context includes
instructions, all five tool definitions, opening, model settings, messages and usage.
The export API combines those with session_start/session_end, report and persisted
events. Interrupted contexts remain explicitly incomplete. See [report API](AGENT-REPORT-API.md).

Graph results are limited to 16 KiB; indexes to 64 KiB. Both reject oversized
results without dropping topology or pagination. Detail results use the existing
2 KiB truncation rule. HTTP reads have a 5-second timeout and 512 KiB ceiling;
tool calls retain their 10-second timeout and count toward the 20-call budget.
Schema, timestamp, HTTP and connection errors are visible; no recording fallback
is used. This change was not tested, built, reviewed or run against a model, as requested.
