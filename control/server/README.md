# Guard Room HTTP server

程式入口是 `main:app`。目前預設提供真實 monitor graph、日誌、快照歷史與 SQLite 調查。mock 必須明確設定 `NIGHTWATCH_MOCK_DATA=1`。實作缺口見 [INTEGRATION.md](../../INTEGRATION.md)。

## 啟動

從 repo 根目錄執行：

```sh
./guardroom/restart.sh
```

此命令使用 Docker Compose，預設 port 9999。若要在 host 開發，先以 `./guardroom/restart.sh --close` 停止容器，再安裝既有鎖定依賴並啟動單一 worker：

```sh
uv sync --project control/server --locked --offline
control/server/.venv/bin/python -m uvicorn main:app --app-dir control/server --host 127.0.0.1 --port 9999
```

| 變數 | 用途 |
| --- | --- |
| `GUARDROOM_CONFIG` | 預設 `guardroom/shop-web.config.json`；控制 monitor→node 對應與保存路徑 |
| `NIGHTWATCH_GRAPH_URL` | 調查用來源，預設 `http://127.0.0.1:9999/api/graph`；改 server 埠時同步設定 |
| `NIGHTWATCH_INVESTIGATION_DB` | 預設 `control/.data/investigations.sqlite3`，另有排他鎖檔 |
| `NIGHTWATCH_MOCK_DATA` | `0`（預設）或 `1`；只切換舊前端 API，不會將 investigation API 換成假模型 |

可選 `NIGHTWATCH_SHOP_URL=http://127.0.0.1:8005`，授權 agent 解除該本機店面的演練故障；工具與驗證邊界見 [system design](../SYSTEM_DESIGN.md)。既有 `/api/faults*` 路由不因此啟用。

Compose 將調查 SQLite 放在 state volume 的 `/app/guardroom/.run/investigations.sqlite3`，重啟保留。Docker 內的 localhost 指容器自身，現有 Compose 未注入 `NIGHTWATCH_SHOP_URL`，修復工具預設關閉；啟用需另行設計符合本機 origin 限制的連線方式。

模型設定見 [control README](../README.md)。server 不自行載入 dotenv；Compose 從 `guardroom/.env` 讀取並注入環境變數。`/docs`、`/openapi.json` 列出實際路由。

## 可用 API

| 路徑 | 行為 |
| --- | --- |
| `GET /health` | HTTP 服務存活 |
| `GET /health/ready` | snapshot 與歷史排程就緒；獨立於被監控節點的健康 |
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

## 調查對話事件

新的調查將每次模型回覆完成後的文字、公開推理摘要與累計用量保存成 `agent.output`、`agent.thinking_summary`、`agent.usage`。仍使用 `investigation_id`、`seq`、`cursor`、`at`、`payload`；文字 payload 為 `message_id`／`text`，用量 payload 為 `usage`（既有欄位）。這是每次回覆的即時更新，並非逐 token 推播。

SSE `/api/investigations/stream` 以外層 `event: agent` 發出新事件，舊客戶端可略過；原有六種仍是 `event: investigation`，只有後者帶 SSE `id`。新客戶端從兩類 payload 的 `cursor` 保存重連位置，REST 回放不推進串流 cursor。

`GET /api/investigations/{id}/events?include_messages=1` 包含上述新事件；預設仍只回傳舊事件。`next_after` 是底層頁面的 seq，頁面被過濾成空陣列時仍須依非 null 的 `next_after` 繼續讀取。舊調查不補造未保存的訊息。

OpenAI Responses 請求公開 reasoning summary；僅發出 summary 文字，不發出 signature、加密內容或 raw reasoning。模型未提供摘要時不產生假摘要。`submit_report` 保持唯一的 graph 調查結構化結案工具，沿用節點／證據驗證與框架重試；沒有成功 `report.submitted` 就不交付成功報告，額度與錯誤仍明確保存。

前端路由、相容策略與驗收記錄見 [調查對話 spec](../../console/specs/investigation-chat-report.md)。

`agent.reasoning_status` 的 payload 為 `{message_id, status: "summary_unavailable"}`，表示 Responses 回傳了推理項目但沒有可讀摘要；不包含 signature、encrypted_content 或 raw_content。它與其他 agent 訊息一樣保存、回放並使用 SSE `agent` frame。
