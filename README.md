<p align="center">
  <img src="docs/readme/nightwatch-hero.png" alt="NightWatch — From signals to understanding. 深夜中的瞭望塔照亮相連的服務節點。" width="100%">
</p>

<h1 align="center">NightWatch</h1>

<p align="center">
  <strong>讓異常有跡可循，讓調查有據可查。</strong><br>
  Turn service signals into evidence-backed investigations.
</p>

<p align="center">
  <a href="#繁體中文">繁體中文</a> ·
  <a href="#english">English</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#integration">Integration</a> ·
  <a href="#roadmap">Roadmap</a>
</p>

<p align="center">
  Function monitoring · Service graph · AI investigation · Persistent evidence<br>
  Hackathon prototype · Local demo · Real service data
</p>

---

## 繁體中文

### 服務出問題時，先知道該往哪裡看。

結帳變慢、錯誤增加、日誌散落在不同服務。NightWatch 的目標，是把這些訊號接成一條能追查的線：**看見異常、調閱證據、提出假設，留下可回看的調查報告。**

我們從一間真的能瀏覽商品、保存購物車與建立訂單的本機商店開始。Python Monitor 收集函式執行事件；Guard Room 將事件整理成服務圖與歷史快照；AI Agent 讀取圖、節點與近期日誌，交付結構化報告。Console 讓人追蹤調查進度，回看事件與模型對話。

**最終願景：從發現問題，一路走到經人批准、可驗證的修復。** 目前已實作的是監測、調查與報告；真實修復執行與修復後驗證仍是未來工作。

### 值得打開它的四個理由

- **有真實場景。** 商店由 gateway、catalog、cart、order 組成，以 SQLite 保存資料，並提供結帳例外、資料庫寫入鎖與延遲三種真實故障演練。
- **調查有證據。** Agent 可讀取服務圖、歷史快照、節點詳情與近期日誌；報告包含發現、假設、限制與下一步，並驗證節點及證據 ID。
- **過程留得下來。** 調查、事件、模型對話與證據保存於 SQLite；後端提供報告與完整調查匯出 API。
- **不把未知塗成綠燈。** 缺少的量測保留為 `null`；模型或上游失敗會顯示錯誤，錄影與 mock 必須明確選用。

### 現階段能依賴什麼？

依 [2026-09-12 對接盤點](docs/readme/integration-status.md)，目前已串起「店面請求 → 部分 Monitor → Guard Room → 調查／報告」。自動偵測會在同一節點連續三次出現符合新鮮度條件的 `warning`／`failing` 時觸發調查；也可手動建立。

**監測尚未覆蓋拆分後的所有服務，因此店面的每張故障卡不保證會觸發調查。** Console 尚無批准修復入口，control 也尚未接到真實修復操作。看到調查報告或 mock 的 recovered 狀態，都不代表服務已被修好。

## English

### When a service breaks, know where to look.

Checkout slows down. Errors climb. Logs live in different services. NightWatch aims to connect those signals into an investigation you can follow: **spot an anomaly, inspect evidence, form hypotheses, and keep a report you can revisit.**

The playground is a working local storefront with products, persistent carts, and orders. Python Monitor captures function events. Guard Room turns them into a service graph and historical snapshots. An AI agent inspects the graph, nodes, and recent logs to produce a structured report. The Console lets people follow investigations and revisit events and model conversations.

**Our destination: a path from detection to human-approved, verifiable recovery.** Monitoring, investigation, and reporting are implemented today. Real repair execution and post-repair verification remain future work.

- **A real playground.** Gateway, catalog, cart, and order services persist data in SQLite. Three fault scenarios exercise checkout exceptions, database write locks, and latency.
- **Evidence you can inspect.** Agent tools read graphs, historical snapshots, node details, and recent logs. Reports capture findings, hypotheses, limitations, and next steps, with node and evidence ID validation.
- **A durable investigation trail.** SQLite stores investigations, events, model conversations, and evidence. Backend APIs expose reports and complete investigation exports.
- **Visible uncertainty.** Missing measurements stay `null`. Model and upstream failures surface as errors; recordings and mocks require explicit selection.

The [2026-09-12 integration audit](docs/readme/integration-status.md) documents the current path: storefront requests → partial monitoring → Guard Room → investigation/report. Automatic detection requires three consecutive fresh `warning`/`failing` observations for a node; investigations can also be started manually.

**Monitoring does not yet cover every split service, so a storefront fault is not guaranteed to trigger an investigation.** The Console has no repair approval flow, and control has no connected repair actuator. A report—or a mock recovered state—is not proof of recovery.

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

商店預設透過共享 JSONL 接入；HTTP sink 是其他 Python 服務可選的接法。圖中的監測範圍仍有限，詳見 [Guard Room 設定說明](guardroom/README.md)。

The storefront uses shared JSONL by default. Other Python services can opt into the HTTP sink. Graph coverage remains partial; see the [Guard Room configuration notes](guardroom/README.md).

## Quick start

以下命令從 repository 根目錄執行。需要 Git、Docker + Compose、GNU Make、Python 與 `uv`；各 Python 元件版本依自己的專案設定，Monitor 最低為 Python 3.11，商店容器使用 Python 3.12。Console 使用標準函式庫，不需 npm 安裝。

Run from the repository root. Have Git, Docker + Compose, GNU Make, Python, and `uv` available. Python version requirements are component-specific: Monitor needs 3.11+, and the storefront container uses 3.12. The Console uses the standard library and needs no npm install.

首次依賴安裝、映像下載與 Docker build 可能需要網路。預先備妥依賴與映像後，本機服務與明確選取的錄影可離線使用；**真實 AI 調查需要可達的模型端點與金鑰**，自動偵測也可能觸發模型呼叫。

Initial dependency installation, image pulls, and Docker builds may need network access. With dependencies and images prepared, local services and explicitly selected recordings can run offline. **Live AI investigation requires a reachable model endpoint and credentials**; automatic detection can also initiate model calls.

```sh
git clone https://github.com/davidleitw/nightwatch-hack.git
cd nightwatch-hack

# Build and start the five storefront containers.
make -C shop-web up

# Start Guard Room. It installs locked dependencies and validates config.
bash guardroom/restart.sh

# Build the Console, then serve the production assets in this terminal.
python3 console/build.py
python3 console/serve.py --port 4173 --control-url http://127.0.0.1:8001
```

| 入口 / Entry | URL | 用途 / Purpose |
| --- | --- | --- |
| 商店 / Storefront | http://127.0.0.1:8080 | 商品、購物袋與示範訂單 / Products, carts, demo orders |
| 故障演練 / Fault playground | http://127.0.0.1:8080/#/events | 三張作用於 order 的故障卡 / Three order-service faults |
| Console | http://127.0.0.1:4173 | 即時調查工作區 / Live investigation workspace |
| Guard Room API | http://127.0.0.1:8001/docs | API 文件 / Interactive API documentation |
| Storefront API | http://127.0.0.1:8000/docs | Gateway API 文件 / Gateway API documentation |

在另一個終端確認服務回應 / Check the services from another terminal:

```sh
curl --fail-with-body http://127.0.0.1:8080/api/health
curl --fail-with-body http://127.0.0.1:8001/health
curl --fail-with-body http://127.0.0.1:8001/api/graph
```

瀏覽商品以產生被監測的請求；空的觀測窗口可能顯示 unknown。商店沒有金流或物流。Guard Room 使用 **單一 worker**；更換埠號時也要調整 `NIGHTWATCH_GRAPH_URL`。

Browse products to generate monitored requests; an empty observation window may show unknown. Checkout has no payment or fulfillment integration. Run Guard Room with **one worker** and update `NIGHTWATCH_GRAPH_URL` if its port changes.

### Enable AI investigation / 啟用 AI 調查

啟動 Guard Room **之前**，在同一個 shell 設定 `NIGHTWATCH_LLM_API_KEY` 或 `OPENAI_API_KEY`，前者優先。可用 `NIGHTWATCH_LLM_ENDPOINT` 與 `NIGHTWATCH_LLM_MODEL` 選擇部署所需端點及模型；預設值與 CLI 選項見 [Agent 說明](control/README.md)。HTTP server 只讀程序環境，不自行載入 `.env`。

**Before** starting Guard Room, set `NIGHTWATCH_LLM_API_KEY` or `OPENAI_API_KEY` in that shell; the former takes precedence. Configure `NIGHTWATCH_LLM_ENDPOINT` and `NIGHTWATCH_LLM_MODEL` for your deployment; defaults and CLI options are in the [Agent guide](control/README.md). The HTTP server reads process environment variables and does not load `.env` itself.

只想先看介面？建置 Console 後，在另一埠明確開啟離線 mock；此模式不能建立真實調查。

Just exploring the UI? After building the Console, explicitly start its offline mock on another port. This mode cannot create live investigations.

```sh
python3 console/serve.py --port 4174 --mock
```

## Integration

### 1. 接入服務訊號 / Send service signals

Python 服務可用 `@monitor(MonitorConfig(...))` 包住要觀測的函式，再選 JSONL 或 `GuardRoomSink` 背景批次傳送。HTTP 日誌入口為 **`POST /api/logs`**，格式為 `nightwatch.log.v1`，不是 OTLP。

Wrap selected Python functions with `@monitor(MonitorConfig(...))`, then use JSONL or background batches through `GuardRoomSink`. The HTTP ingestion endpoint is **`POST /api/logs`**, using `nightwatch.log.v1`, not OTLP.

先在 Guard Room 設定中建立 **`monitor_id` → node 對應**；未知 monitor 的日誌可被接收，但不會自動建立圖節點。生命週期與 sink 關閉範例見 [Monitor 接入指南](control/monitor/README.md)，現行 log schema 見 [log.schema.json](console/schema-draft/log.schema.json)。

Declare the **`monitor_id` → node mapping** in Guard Room first. Logs from unknown monitors can be accepted without creating graph nodes. See the [Monitor integration guide](control/monitor/README.md) for instrumentation and sink shutdown examples, and the [log schema](console/schema-draft/log.schema.json) for the payload format.

### 2. 對接調查 API / Connect an investigation client

| 方法 / Method | Endpoint | 用途 / Purpose |
| --- | --- | --- |
| GET | `/api/graph` | 最新圖；`timestamp` 查歷史 / Latest graph; historical lookup with `timestamp` |
| GET | `/api/graph/snapshots` | 快照索引 / Snapshot index |
| GET | `/api/debug/logs` | 最近保留日誌 / Recent retained logs |
| POST | `/api/investigations` | 建立調查 / Create an investigation |
| GET | `/api/investigations/state` | 工作區狀態 / Workspace state |
| GET | `/api/investigations/stream` | SSE 狀態及調查更新 / SSE state and investigation updates |
| GET | `/api/investigations/{id}/report` | 結構化結案報告 / Structured final report |
| GET | `/api/investigations/{id}/export` | 調查、證據、對話與用量 / Investigation, evidence, context, and usage |
| GET | `/events` | 即時 log 與相容事件串流 / Live logs and compatibility event stream |

真實調查範例，需要上方的模型設定 / Live investigation example; requires the model configuration above:

```sh
curl --fail-with-body http://127.0.0.1:8001/api/investigations \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"readme-manual-001","trigger":{"source":"manual","reason":"Inspect current service anomalies"}}'
```

重送相同 `request_id` 與內容會取回同一調查；新調查請換 ID，重試則保留原 ID 及內容。同時只執行一件調查。報告尚未結案回 `409`，未知 ID 回 `404`。SSE 支援 `after` 或 `Last-Event-ID` 續傳，完整介面見 [HTTP API 說明](control/server/README.md)。

Repeat the same `request_id` and payload to retrieve the same investigation. Use a new ID for a new investigation; keep both unchanged for retries. Only one investigation runs at a time. Reports return `409` until finalized and `404` for unknown IDs. SSE supports resuming with `after` or `Last-Event-ID`. See the [HTTP API guide](control/server/README.md) for the full interface.

### 3. 對接商店 / Connect to the storefront

外部 client 使用 gateway `:8000`，或 Nginx 的 `:8080/api/`；catalog、cart、order 僅在 Compose 內部開放。商品、購物車、checkout 與 `Idempotency-Key` 的完整規則見 [商店 API 契約](shop-web/docs/API.md)。

External clients use the gateway on `:8000` or the Nginx `/api/` proxy on `:8080`. Catalog, cart, and order stay inside the Compose network. See the [storefront API contract](shop-web/docs/API.md) for products, carts, checkout, and `Idempotency-Key` behavior.

故障卡使用商店的 **`GET/POST/DELETE /api/demo-faults`**，目前尚未與 control 的 `/api/faults*` 接通。後者及舊批准操作在 live 模式回 `503`。請勿將兩套端點視為可互換。

Fault cards use the storefront's **`GET/POST/DELETE /api/demo-faults`**. These are not yet connected to control's `/api/faults*` APIs, which return `503` in live mode, as do legacy approval operations. The two interfaces are not interchangeable.

## Roadmap

以下是依現行對接缺口提出的工作順序，並非已完成的能力或交付時程。

These proposed priorities follow the current integration gaps. They are future work, not shipped capabilities or delivery commitments.

| 優先 / Priority | 下一步 / Next step | 完成後的價值 / Outcome |
| --- | --- | --- |
| 1 | 對齊 catalog／cart／order 的 Monitor 與圖設定 / Align instrumentation and graph mappings | 故障可對應到實際受影響服務 / Relate faults to the services they affect |
| 2 | 串接 control 與商店故障控制 / Connect control to storefront fault operations | 演練有真實操作、狀態與解除流程 / Real fault lifecycle and restoration controls |
| 3 | 人工批准、修復執行、修復後觀測窗口 / Add approval, repair execution, and an observation window | 用證據確認修復成效 / Verify recovery with evidence |
| 4 | 補齊基線、指標、trace 與獨立 health probe / Add baselines, metrics, traces, and independent probes | 讓調查取得更完整的觀測資料 / Give investigations more complete observations |
| 5 | 全歷史日誌查詢、完整實驗稽核與 Console 匯出入口 / Add historical log search, experiment audits, and Console export | 更完整地回看與分享事故 / Revisit and share incidents with fuller context |

想一起做？先從 [對接盤點](docs/readme/integration-status.md) 挑一個缺口，確認對應介面，再提出範圍清楚的 issue 或 PR。現行 `contracts/` 的 schema 與錄影仍有程式依賴；保留它們不代表其中的舊功能已上線。

Want to contribute? Pick a gap from the [integration audit](docs/readme/integration-status.md), check the relevant interface, and open a focused issue or PR. Schemas and recordings in `contracts/` still support code paths; their presence does not mean legacy features are live.

## Explore the project

| 位置 / Location | 內容 / What you will find |
| --- | --- |
| [shop-web/](shop-web/README.md) | React 商店、FastAPI gateway 與三個業務服務 / Storefront, gateway, and three business services |
| [control/monitor/](control/monitor/README.md) | Python 函式監測、JSONL 與 HTTP sink / Function instrumentation and event delivery |
| [control/](control/README.md) | AI 調查工具、模型設定與 CLI / Investigation tools, model settings, and CLI |
| [control/server/](control/server/README.md) | Guard Room HTTP API、SQLite、SSE / APIs, persistence, and event streams |
| [guardroom/](guardroom/README.md) | 本機啟動與監測映射設定 / Local startup and monitor mappings |
| [console/](console/README.md) | 真實調查工作區與明確選用的錄影 / Live workspace and opt-in recordings |
| [Integration status](docs/readme/integration-status.md) | 真實對接、mock 與限制的詳細盤點 / Detailed live integration, mock, and limitation audit |

`gate/` 及根目錄的 `task.sh`、`run-task.sh`、`setup.sh`、`hackathon.conf` 是既有協作工具，並非服務啟動入口。

`gate/` and the root `task.sh`, `run-task.sh`, `setup.sh`, and `hackathon.conf` support the existing collaboration workflow; they are not service startup entry points.

<p align="center"><strong>從看見異常，到理解原因。<br>From signals to understanding.</strong></p>
