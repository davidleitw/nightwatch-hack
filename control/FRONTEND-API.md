# 前端 API：整合 Guard Room

以已合併的 PR #6（`267e19d`）為基礎，其他前端介面現在與 graph 共用
`control/server/main.py` 的 FastAPI app、port 與 OpenAPI。沒有另一個 HTTP server。

本輪新增 `server/frontend_api.py`、`server/frontend_mock.py`，並在既有 app 註冊路由。
`/api/graph` 的 handler、參數與資料生成維持隊友的實作。

## 啟動與假資料 flag

在 `control/server/` 使用既有鎖定依賴建立的虛擬環境：

```sh
# 預設關閉其他前端 API 的假資料
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8001

# 明確啟用假資料與記憶體模擬操作
NIGHTWATCH_MOCK_DATA=1 .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8001
```

不使用 `--reload`。以一個 worker 執行；模擬狀態存在該程序的記憶體，重啟即重置。
`NIGHTWATCH_MOCK_DATA` 僅接受 `0`／`1`，其他值直接阻止啟動，避免設定錯字被忽略。
不變更依賴、版本或 lockfile。既有 restart script 的使用方式見 `guardroom/README.md`。

flag 只控制本輪新增的 API。隊友原有 `/api/graph` 仍然是 dummy，且不受此 flag 影響。
新增 API 的每筆 JSON／SSE 回應都有 `X-NightWatch-Mock: true|false`；開啟時也會印出
`[MOCK DATA ENABLED]`，中文故障卡、事故摘要與時間線標示「模擬資料」。

關閉時：`/health` 回 200，`/api/readiness` 回 200 與 `ready:false`，其餘新資料／操作
端點回 503，明確說明真實後端尚未接入。不會自動退回假資料，也不會啟動 agent 或呼叫真實服務。

## 實際 API 清單

同一份 `/openapi.json` 包含以下 22 條 API 路徑；`/docs` 與 `/redoc` 是 FastAPI 原有文件入口。

| 提供者 | 方法 | 路徑 | 用途 |
|---|---|---|---|
| 隊友既有 | GET | `/api/graph?state=normal|problem` | graph dummy，預設 normal；無效 state 回 422 |
| 本輪新增 | GET | `/health` | HTTP 程序存活 |
| 本輪新增 | GET | `/api/readiness` | 就緒檢查 |
| 本輪新增 | GET | `/api/state` | 初始狀態、graph、事故與能力表 |
| 本輪新增 | GET | `/api/capabilities` | 節點與能力表 |
| 本輪新增 | GET | `/api/debug/logs?service=&limit=` | 最近的日誌 |
| 本輪新增 | GET | `/api/faults/catalog` | 五張現行故障卡的模擬目錄 |
| 本輪新增 | GET | `/api/faults/instances` | 本輪故障實例 |
| 本輪新增 | POST | `/api/faults` | 模擬注入故障並建立待批准事故 |
| 本輪新增 | POST | `/api/faults/instances/{id}/restore` | 模擬還原單一故障，不算修復 |
| 本輪新增 | POST | `/api/faults/restore-all` | 模擬清理；事故中需 force 才中止 |
| 本輪新增 | POST | `/api/rounds` | 清理完成後開始下一輪 |
| 本輪新增 | GET | `/api/rounds/operations/{id}` | 操作結果 |
| 本輪新增 | GET | `/api/incidents` | 模擬事故清單，新到舊 |
| 本輪新增 | GET | `/api/incidents/{id}` | 事故詳情，與 state.incident 同形狀 |
| 本輪新增 | GET | `/api/incidents/{id}/snapshots` | 快照介面；本版沒有釘住快照，陣列為空 |
| 本輪新增 | GET | `/api/incidents/{id}/timeline` | 事故時間線 |
| 本輪新增 | GET | `/api/incidents/{id}/report` | 結案報告；未結案回 404 |
| 本輪新增 | GET | `/api/incidents/{id}/events` | 事故事件紀錄 |
| 本輪新增 | POST | `/api/incidents/{id}/approve` | 模擬批准、修復、驗證、結案 |
| 本輪新增 | POST | `/api/incidents/{id}/abort` | 模擬中止並還原 |
| 本輪新增 | GET | `/events?cursor=<run_id>:<revision>` | SSE 與記憶體事件重播 |

本輪沒有新增任何 `/api/graph/*` 子路徑，也沒有改寫 `/api/graph`。
未開聊天 API、Monitor 接收端點或 SSE `log`；後者仍待正式契約納入。

## 模擬模式的操作與回應

- 初始沒有事故；五張卡 ID 與目前 `contracts/cards.yaml` 相同。目錄曲線、所有量測與結果只供畫面示範。
- `POST /api/faults` 需 `request_id`、`card_id`，可帶 `lease_secs`（整數，至少 901）。成功回 202 與 `instance_id`、`operation_id`。
- `POST /api/incidents/{id}/approve` 需 `request_id`、`proposal_id`；其他寫入也需要 `request_id`；`restore-all` 另需布林 `force`。
- 寫入回 202 與 `operation_id`，換輪另含 `run_id`。操作在記憶體內立即完成，不模擬真實維修等待或 lease 計時。
- 相同 request_id、路徑與內容重送，回原本結果；同一 request_id 改內容回 400。未知卡片／資源回 404，狀態不允許操作回 409。
- 批准會依序產生批准、執行、驗證與結案事件；兩個驗證窗口都是明示的假資料。
- 結案後才填 incident.card_id，才提供 report。換輪保留程序內的事故清單；程序重啟後所有模擬事故消失。

錯誤沿用 `{"error":{"code":"...","message_zh":"...","details":{}}}`。
不支援／重複參數與無效 JSON 回 400。日誌 limit 預設 20；假設本版接受 1–20000。
前端透過同來源呼叫 API，本輪未新增 CORS。

## Graph 與 SSE 分工

`state.graph_now` 直接取同一個 `dummy_graph()` 的 normal 資料，不另造 graph。
`capabilities.nodes` 從同份 graph 產生相同 ID 集合；假設 mock layout 暫以三欄排列。
隊友 graph 的 `state=problem` 只影響那一次 graph 請求；本輪模擬事故不改它的全域狀態。

SSE 先送完整 `state`，每 2 秒送 `ping`；操作後推送 `incident`、`faults`、`readiness`，
換輪推 `run` 與新 `state`。僅 incident 帶 `id: <run_id>:<revision>`。
同一輪 cursor 補送其後的事件；新 run／重啟時以完整 state 重新同步。
state 可能已是最新版本，前端補歷史事件時不得把投影退回舊 revision。

本輪不產生 SSE graph 或 log，避免自行接管隊友的 graph；graph SSE 仍需對齊負責方。
真實資料提供者、事故引擎及 Monitor bridge 都尚未接入新增介面。

## 契約待確認

- 目前以 HTTP 503 + `internal` 表達真實來源尚未接入，正式契約是否接受？
- SSE graph 由哪個模組負責推送？目前 REST graph 已存在，新增 SSE 尚未接它。
- SSE log、Monitor→node 對應，以及動態節點 layout 何時納入正式契約？
