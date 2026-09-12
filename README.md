<p align="center">
  <img src="docs/readme/nightwatch-hero.png" alt="NightWatch — From signals to understanding. A watchtower illuminates connected service nodes at night." width="100%">
</p>

<h1 align="center">NightWatch</h1>

<p align="center">
  <strong>Turn service signals into evidence-backed investigations.</strong>
</p>

<p align="center">
  <strong>English</strong> · <a href="README.zh-TW.md">繁體中文</a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#integration">Integration</a> ·
  <a href="#roadmap">Roadmap</a>
</p>

<p align="center">
  Function monitoring · Service graph · AI investigation · Persistent evidence<br>
  Hackathon prototype · Local demo · Real service data
</p>

---

## When a service breaks, know where to look.

Checkout slows down. Errors climb. Logs live in different services. NightWatch aims to connect those signals into an investigation you can follow: **spot an anomaly, inspect evidence, form hypotheses, and keep a report you can revisit.**

The playground is a working local storefront with products, persistent carts, and orders. Python Monitor captures function events. Guard Room turns them into a service graph and historical snapshots. An AI agent inspects the graph, nodes, and recent logs to produce a structured report. The Console lets people follow investigations and revisit events and model conversations.

**Our destination: a path from detection to human-approved, verifiable recovery.** Monitoring, investigation, and reporting are implemented today. Local demo-fault deactivation and health queries can be explicitly enabled; general repair, approval, and trustworthy recovery verification remain future work.

- **A real playground.** Gateway, catalog, cart, and order services persist data in SQLite. Three fault scenarios exercise checkout exceptions, database write locks, and latency.
- **Evidence you can inspect.** Agent tools read graphs, historical snapshots, node details, and recent logs. Reports capture findings, hypotheses, limitations, and next steps, with node and evidence ID validation.
- **A durable investigation trail.** SQLite stores investigations, events, model conversations, and evidence. Backend APIs expose reports and complete investigation exports.
- **Visible uncertainty.** Missing measurements stay `null`. Model and upstream failures surface as errors; recordings and mocks require explicit selection.

The [2026-09-12 integration audit](docs/readme/integration-status.md) documents the current path: storefront requests → partial monitoring → Guard Room → investigation/report. Automatic detection requires three consecutive fresh `warning`/`failing` observations for a node; investigations can also be started manually.

**Checkout request, logic, and DB writes now appear in a six-node graph; catalog/cart internals remain partially covered.** An active fault needs checkout traffic to produce observations; automatic investigation still depends on consecutive fresh anomalies. The Console has no repair approval flow. With `NIGHTWATCH_SHOP_URL` configured, the agent can deactivate faults in the designated local demo storefront; this does not establish business recovery. A report—or a mock recovered state—is not proof of recovery.

## Architecture

```text
Storefront: React → FastAPI gateway → catalog / cart / order → SQLite
                                      |
                            instrumented functions only
                                      v
                               Python Monitor
                                      |
                         shared JSONL or HTTP log sink
                                      v
                            Guard Room HTTP API
                           graph / logs / snapshots
                                      |
                           manual or auto detection
                                      v
                               AI investigation
                                      |
                         report / evidence / SQLite
                                      |
                              HTTP + SSE updates
                                      v
                                   Console
```

The storefront uses shared JSONL by default. Other Python services can opt into the HTTP sink. Graph coverage remains partial; see the [Guard Room configuration notes](guardroom/README.md).

## Quick start

Run from the repository root. Have Git, Docker + Compose, Python 3, curl, and lsof available. Dependencies are installed inside the containers: Python 3.12 for the storefront and 3.13 for Guard Room. The Console uses the standard library and needs no npm install.

Initial dependency installation, image pulls, and Docker builds may need network access. With dependencies and images prepared, local services and explicitly selected recordings can run offline. **Live AI investigation requires a reachable model endpoint and credentials**; automatic detection can also initiate model calls.

```sh
git clone https://github.com/davidleitw/nightwatch-hack.git
cd nightwatch-hack

# Build and restart Shop, Guard Room, and Console.
./restart.sh

# Reuse Docker images, or stop all services while preserving data.
# ./restart.sh --open
# ./restart.sh --close
```

The script preserves existing host ports; the table lists first-deployment defaults. Override them with `FRONTEND_PORT=8081 BACKEND_PORT=8001 PORT=9999 CONSOLE_PORT=4173 ./restart.sh`. Console runs in the background on the host, with PID/log files in `.run/`. Restarting interrupts connections and active investigations while preserving Docker volumes and monitor logs. See the [deployment guide](guardroom/README.md).

| Entry | URL | Purpose |
| --- | --- | --- |
| Storefront | http://127.0.0.1:8080 | Products, carts, demo orders |
| Fault playground | http://127.0.0.1:8080/#/events | Three order-service faults |
| Console | http://127.0.0.1:4173 | Live investigation workspace |
| Guard Room API | http://127.0.0.1:9999/docs | Interactive API documentation |
| Storefront API | http://127.0.0.1:8000/docs | Gateway API documentation |

Check the services from another terminal:

```sh
curl --fail-with-body http://127.0.0.1:8080/api/health
curl --fail-with-body http://127.0.0.1:9999/health
curl --fail-with-body http://127.0.0.1:9999/api/graph
```

Browse products to generate monitored requests; no completed events in the 60-second window means unknown, not recovery. Checkout has no payment or fulfillment integration. Run Guard Room with **one worker** and keep the container graph URL on internal port 9999; host CLI clients must use the published port.

### Enable AI investigation

**Before** starting Guard Room, set `NIGHTWATCH_LLM_API_KEY` or `OPENAI_API_KEY` in that shell; the former takes precedence. Configure `NIGHTWATCH_LLM_ENDPOINT` and `NIGHTWATCH_LLM_MODEL` for your deployment; defaults and CLI options are in the [Agent guide](control/README.md). Alternatively, put settings in the Git-ignored `guardroom/.env` for Compose to inject. The HTTP server itself does not load dotenv; the CLI’s `control/.env` is not automatically used by the container.

Just exploring the UI? After building the Console, explicitly start its offline mock on another port. This mode cannot create live investigations.

```sh
python3 console/serve.py --port 4174 --mock
```

## Integration

### 1. Send service signals

Wrap selected Python functions with `@monitor(MonitorConfig(...))`, then use JSONL or background batches through `GuardRoomSink`. The HTTP ingestion endpoint is **`POST /api/logs`**, using `nightwatch.log.v1`, not OTLP.

Declare the **`monitor_id` → node mapping** in Guard Room first. Logs from unknown monitors can be accepted without creating graph nodes. See the [Monitor integration guide](control/monitor/README.md) for instrumentation and sink shutdown examples, and the [log schema](console/schema-draft/log.schema.json) for the payload format.

### 2. Connect an investigation client

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/graph` | Latest graph; historical lookup with `timestamp` |
| GET | `/api/graph/snapshots` | Snapshot index |
| GET | `/api/debug/logs` | Recent retained logs |
| POST | `/api/investigations` | Create an investigation |
| GET | `/api/investigations/state` | Workspace state |
| GET | `/api/investigations/stream` | SSE state and investigation updates |
| GET | `/api/investigations/{id}/report` | Structured final report |
| GET | `/api/investigations/{id}/export` | Investigation, evidence, context, and usage |
| GET | `/events` | Live logs and compatibility event stream |

Live investigation example; requires the model configuration above:

```sh
curl --fail-with-body http://127.0.0.1:9999/api/investigations \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"readme-manual-001","trigger":{"source":"manual","reason":"Inspect current service anomalies"}}'
```

Repeat the same `request_id` and payload to retrieve the same investigation. Use a new ID for a new investigation; keep both unchanged for retries. Only one investigation runs at a time. Reports return `409` until finalized and `404` for unknown IDs. Investigation SSE supports resuming with `after` or `Last-Event-ID`; monitor logs are not replayed. See the [HTTP API guide](control/server/README.md) for the full interface.

### 3. Connect to the storefront

External clients use the gateway on `:8000` or the Nginx `/api/` proxy on `:8080`. Catalog, cart, and order stay inside the Compose network. See the [storefront API contract](shop-web/docs/API.md) for products, carts, checkout, and `Idempotency-Key` behavior.

Fault cards use the storefront's **`GET/POST/DELETE /api/demo-faults`**. The repair tools support null leases for persistent faults. Docker Compose leaves repair disabled, and container localhost does not reach the host storefront. For a host CLI/server, setting `NIGHTWATCH_SHOP_URL` enables `get_demo_faults`, `deactivate_demo_fault`, and `check_shop_health` for the designated local demo. See the [demo repair boundaries](control/SYSTEM_DESIGN.md). Legacy control `/api/faults*` and approval operations still return `503` in live mode; the interfaces are not interchangeable.

## Roadmap

These proposed priorities follow the current integration gaps. They are future work, not shipped capabilities or delivery commitments.

| Priority | Next step | Outcome |
| --- | --- | --- |
| 1 | Extend catalog/cart instrumentation and order health mapping | Relate faults to the services they affect |
| 2 | Harden concurrent demo-fault operations and lifecycle | Strengthen the existing local deactivation tools |
| 3 | Add approval, repair execution, and an observation window | Verify recovery with evidence |
| 4 | Add baselines, metrics, traces, and independent probes | Give investigations more complete observations |
| 5 | Add historical log search, experiment audits, and Console export | Revisit and share incidents with fuller context |

Want to contribute? Pick a gap from the [integration audit](docs/readme/integration-status.md), check the relevant interface, and open a focused issue or PR. Schemas and recordings in `contracts/` still support code paths; their presence does not mean legacy features are live.

## Explore the project

| Location | What you will find |
| --- | --- |
| [shop-web/](shop-web/README.md) | Storefront, gateway, and three business services |
| [control/monitor/](control/monitor/README.md) | Function instrumentation and event delivery |
| [control/](control/README.md) | Investigation tools, model settings, and CLI |
| [control/server/](control/server/README.md) | APIs, persistence, and event streams |
| [guardroom/](guardroom/README.md) | Docker deployment and monitor mappings |
| [console/](console/README.md) | Live workspace and opt-in recordings |
| [Integration status](docs/readme/integration-status.md) | Detailed live integration, mock, and limitation audit |
| [INTEGRATION.md](INTEGRATION.md) | Current implementation and historical verification records |
| `contracts/schemas/`, `contracts/fixtures/` | Runtime schemas and recordings; obsolete architecture documents removed |
| `gate/` | PR and harness tools, separate from application runtime |

`gate/` and the root `task.sh`, `run-task.sh`, `setup.sh`, and `hackathon.conf` support the existing collaboration workflow; they are not service startup entry points. Legacy numbered task materials have been removed.

<p align="center"><strong>From signals to understanding.</strong></p>
