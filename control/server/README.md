# Guard Room HTTP server

程式入口是 `main:app`。目前預設提供真實 monitor graph、日誌、快照歷史與 SQLite 調查。mock 必須明確設定 `NIGHTWATCH_MOCK_DATA=1`。實作缺口見 [INTEGRATION.md](../../INTEGRATION.md)。

## 啟動

從 repo 根目錄執行：

```sh
bash guardroom/restart.sh
```

或安裝既有鎖定依賴後，直接啟動單一 worker：

```sh
uv sync --project control/server --locked --offline
control/server/.venv/bin/python -m uvicorn main:app --app-dir control/server --host 127.0.0.1 --port 8001
```

| 變數 | 用途 |
| --- | --- |
| `GUARDROOM_CONFIG` | 預設 `guardroom/shop-web.config.json`；控制 monitor→node 對應與保存路徑 |
| `NIGHTWATCH_GRAPH_URL` | 調查用來源，預設 `http://127.0.0.1:8001/api/graph`；改 server 埠時同步設定 |
| `NIGHTWATCH_INVESTIGATION_DB` | 預設 `control/.data/investigations.sqlite3`，另有排他鎖檔 |
| `NIGHTWATCH_MOCK_DATA` | `0`（預設）或 `1`；只切換舊前端 API，不會將 investigation API 換成假模型 |

可選 `NIGHTWATCH_SHOP_URL=http://127.0.0.1:8005`，授權 agent 解除該本機店面的演練故障；工具與驗證邊界見 [system design](../SYSTEM_DESIGN.md)。既有 `/api/faults*` 路由不因此啟用。

模型設定見 [control README](../README.md)。server 不自行載入 dotenv。`/docs`、`/openapi.json` 列出實際路由。

## 可用 API

| 路徑 | 行為 |
| --- | --- |
| `GET /health` | HTTP 服務存活 |
| `GET /api/graph` | 最新真實快照；`timestamp` 讀歷史，`state=normal/problem` 明確讀假圖 |
| `GET /api/graph/snapshots` | `limit`、`before_seq` 分頁列出保留快照 |
| `POST /api/logs` | 接收 `nightwatch.log.v1` 日誌，去重、保存並推播 |
| `GET /api/debug/logs` | `service`、`limit` 查最新保留日誌 |
| `GET /api/state`、`/api/readiness`、`/api/capabilities` | 相容舊畫面的真實唯讀投影；readiness 不代表整套系統可修復 |
| `GET /events` | 舊 state／graph／incident 與即時 log、ping |
| `POST /api/investigations` | 建立持久化調查；body 為 `request_id` 與可選 `trigger` |
| `GET /api/investigations` | 調查清單；`limit`、`before` 分頁 |
| `GET /api/investigations/state` | 當前調查、graph、來源錯誤與 cursor |
| `GET /api/investigations/stream` | state／graph／investigation／ping；`after` 或 Last-Event-ID 續傳 |
| `GET /api/investigations/{id}` | 調查詳情、evidence、usage |
| `GET /api/investigations/{id}/events` | 已保存事件；`after`、`limit` 分頁 |
| `GET /api/investigations/{id}/report` | 結案報告；未結案 409，未知 ID 404 |
| `GET /api/investigations/{id}/snapshots` | 作為 evidence 保存的快照 |
| `GET /api/investigations/{id}/context` | 已保存模型對話 |
| `GET /api/investigations/{id}/export` | 匯出調查、報告、事件、context、evidence 與 usage |

`POST /api/investigations` 範例：`{"request_id":"unique-id","trigger":{"source":"manual","reason":"檢查目前異常"}}`。同 request_id 與相同內容重送會取回同一調查，內容不同則衝突。同時只執行一件調查；啟動時把未完成紀錄標為 interrupted。

## 自動偵測

每 5 秒刷新 graph；同一節點連續三次符合新鮮度條件的 warning／failing 才開調查。SQLite 保存異常旗標，避免同一段異常重複開案；確認恢復且沒有執行中調查才解除。舊／重複快照與來源失敗打斷累計。

Graph URL 帶 query 或 `NIGHTWATCH_MOCK_DATA=1` 時停用自動偵測。這只停用自動觸發，手動建立調查仍使用設定的模型。Prometheus／Jaeger 不是目前 detector 的資料源。

## 尚未實作

`/api/faults*`、換輪、操作進度、批准與中止只有 mock 實作，live 回 503。舊 `/api/incidents/{id}/report`、`timeline` 缺完整修復稽核，live 回 503；應讀 `/api/investigations/{id}/report`。完整端點清單與觀測限制見 [盤點](../../INTEGRATION.md)。
