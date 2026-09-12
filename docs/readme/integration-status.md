# Integration status / 對接狀態

Snapshot: 2026-09-12. This document summarizes the existing local integration audit and component documentation. It is a documentation baseline, not a new end-to-end test result. 本文件整理既有本機盤點與元件說明，代表文件基準，不是這次重新執行的端到端驗證。

The source audit is [INTEGRATION.md](../../INTEGRATION.md), originally recorded in `181944c` and now included on master through MR #27. The tables below also incorporate the Docker and checkout-monitoring integration. MR #27 additionally introduced opt-in local demo-fault tools, so its repair updates below supersede the older absence-of-repair statements.

來源是 [INTEGRATION.md](../../INTEGRATION.md)，最初記錄於 `181944c`，現已隨 MR #27 納入主線。下表已納入 Docker 與結帳監測整合；MR #27 另加入可明確啟用的本機演練故障工具，因此以下更新取代舊盤點中完全沒有修復接線的敘述。

Docker Compose 預設未啟用修復工具，容器 localhost 無法沿用 host Shop URL。本機 CLI／host server 設定 `NIGHTWATCH_SHOP_URL` 後，Agent 可使用 `get_demo_faults`、`deactivate_demo_fault` 與 `check_shop_health`，限指定本機店面的演練操作；通用修復、人工批准與可信恢復驗證仍未完成。永久故障的 null 租期與數值租期皆受支援。解除故障不等於成功結帳或服務恢復，完整限制見 [SYSTEM_DESIGN.md](../../control/SYSTEM_DESIGN.md)。

Docker Compose leaves these tools disabled; container localhost does not reach the host storefront. For a host CLI/server with `NIGHTWATCH_SHOP_URL` configured, the agent can inspect/deactivate demo faults and query health in the designated local storefront. General repair, approval, and trustworthy recovery verification remain incomplete. Null leases for persistent faults are supported alongside numeric leases. Clearing a fault does not prove successful checkout or service recovery; see [SYSTEM_DESIGN.md](../../control/SYSTEM_DESIGN.md).

## Available / 已有實作

| Area / 項目 | Current behavior / 目前行為 |
| --- | --- |
| Storefront / 商店 | React → FastAPI gateway → catalog/cart/order, with SQLite persistence and retryable checkout using an idempotency key. 商品、購物車與訂單真實保存；結帳使用冪等 key 重試。 |
| Fault playground / 故障演練 | `GET/POST/DELETE /api/demo-faults` operates three order-service faults: `checkout_exception`, `database_write_lock`, `checkout_delay`. 三張卡作用於 order；持續啟用直到手動解除或程序結束。 |
| Monitoring / 監測 | Function lifecycle events flow through JSONL or an optional HTTP sink. Guard Room projects configured monitors into graphs and retains historical snapshots. 函式事件經設定映射為圖，並保存歷史快照。 |
| Logs / 日誌 | `POST /api/logs` accepts `nightwatch.log.v1`; `GET /api/debug/logs` reads recent retained logs; `/events` streams log events. Storefront ingestion currently uses shared JSONL. 商店預設以共享 JSONL 接入。 |
| Investigation / 調查 | Manual creation, persistent anomaly detection, saved events/context/evidence, structured reports, and backend exports. 支援手動／自動觸發、事件與對話保存、結構化報告及後端匯出。 |
| Agent tools / 工具 | `get_graph`, `list_graph_snapshots`, `get_node_detail`, `search_logs`, `submit_report`. Model/source errors are surfaced without automatic replay fallback. 真模型或來源失敗會顯示錯誤，不自動切換錄影。 |
| Console / 介面 | Live investigation state, history, details, submitted reports, context, and SSE. Background tabs pause streams and resume on visibility. 調查可依 cursor 續接；monitor log 不補送背景期間資料。 |

## Gaps / 尚待對接

| Gap / 缺口 | Consequence / 影響 |
| --- | --- |
| Graph coverage / 圖覆蓋 | Six nodes and three edges cover health, products, order DB reads, and checkout request → logic → DB writes. Catalog/cart internals remain partially covered; `shop.order.health` has no separate node mapping. 結帳故障已可觀測，仍須實際送出請求才產生訊號；無完成事件的 60 秒窗口顯示 unknown。 |
| Fault control / 故障控制 | Legacy control fault routes remain unavailable; opt-in agent demo-fault tools are connected. Console's proxy permits investigation creation but not fault-control POSTs. Agent 可明確啟用本機解除，舊故障介面仍未串接。 |
| Repair / 修復 | Local demo-fault deactivation is available when explicitly enabled; no general actuator, approval-to-execution flow, post-repair observation window, or trusted recovery baseline. 可解除本機演練故障，尚無通用修復與成效驗證閉環。 |
| Measurements / 量測 | No connected Prometheus/Jaeger or independent health probe. `saturation=null`; edge measurements are `null` with `observed=false`. `alive` means monitor events occurred in the window. 缺失量測不是零，沒有事件不代表服務已死。 |
| Readiness / 就緒狀態 | Docker `/health/ready` checks graph/history processing. Legacy readiness/capabilities do not describe all working investigation tools. `ready=false` and `model.available=false` are not sufficient to diagnose model availability. 不能只靠這些旗標判斷模型一定不可用。 |
| Log history / 日誌歷史 | `search_logs` filters a bounded recent batch, not a complete historical archive. 查無資料不能推論沒有故障。 |
| Experiment audit / 演練稽核 | Investigation reports exist; a complete fault/approval/repair/verification timeline does not. Console has no dedicated export action yet. 調查報告不同於完整修復稽核。 |

The legacy control routes `/api/faults*`, round operations, approval/abort, and incident report/timeline have no live implementation of the corresponding operation and return `503` for supported lookups (unknown incident IDs may return `404` first). `abort` does not stop a real investigation worker.

舊 control 故障、換輪、批准／中止及 incident 報告／時間線目前沒有相應的真實操作；已知調查的相關操作回 `503`，未知 ID 可能先回 `404`。`abort` 不會中止真實調查 worker。請使用 `/api/investigations/{id}/report` 讀調查報告。

No routes are registered for `GET /api/graph/history`, `POST /api/chat/messages`, `POST /internal/otlp/v1/logs`, or `POST /internal/alerts/grafana`. Graph history uses `/api/graph/snapshots` and `/api/graph?timestamp=...`.

上述 chat、OTLP、Grafana 告警與舊 graph history 路由未註冊；歷史圖應使用 snapshots／timestamp 介面。

## Explicit demos / 明確選用的示範

| Selector / 開關 | Scope / 範圍 |
| --- | --- |
| `NIGHTWATCH_MOCK_DATA=1` | Simulates legacy control operations; does not replace investigation models. 舊 control API 模擬，不會把 investigation API 換成假模型。 |
| `/api/graph?state=normal` or `state=problem` | Fixed graph preview. 明確選取的假圖預覽。 |
| `python3 console/serve.py --mock` | Offline UI demo; live investigation creation unavailable. 離線介面演示，不能建立真實調查。 |
| `/?source=recording` | Recorded incident playback. 歷史事故錄影。 |
| Agent `--replay-model` | Scripted model plus recorded tools, offline. 腳本模型與錄影工具。 |
| Agent `--fixture ... --model` | Real model with recorded tools; not live-source validation. 真模型搭配錄影資料，不是真實來源驗證。 |

Demo orders are persisted, but no payment or fulfillment is connected. 示範訂單有真實資料保存，沒有金流或物流。

## Interface guides / 介面文件

- [Storefront contract / 商店 API 契約](../../shop-web/docs/API.md)
- [Monitor integration / 監測接入](../../control/monitor/README.md)
- [Guard Room configuration / 圖設定](../../guardroom/README.md)
- [Guard Room HTTP API](../../control/server/README.md)
- [Agent CLI and model configuration / Agent 與模型設定](../../control/README.md)
- [Console startup / 介面啟動](../../console/README.md)

Older component documents may still describe planned features or past test runs. Use the availability boundaries above when reading them; those historical test records are not verification performed for this README change.

較舊的元件文件可能包含規劃功能或過往測試紀錄；閱讀時請配合上方的能力範圍。歷史測試紀錄不是這次 README 工作執行的驗證。
