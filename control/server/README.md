# Guard Room server 開發入口

這裡是 Guard Room 的 FastAPI 實作，使用獨立 uv 專案，需 Python 3.11+。
`guardroom/` 放操作 config 與啟動腳本，`control/server/` 放 server 程式。
Graph 功能行為、設定預設值與操作限制統一以 [Guard Room README](../../guardroom/README.md) 為準。

## 本機開發

從 repo 根目錄執行：

```sh
cd control/server
uv sync --locked
uv run uvicorn main:app --reload --host 127.0.0.1 --port 8001
```

啟動後可開啟 `http://127.0.0.1:8001/docs`，或讀取 `/openapi.json` 查看實際 API 定義。
背景啟動／重啟方式、config 路徑與多 instance 限制見 Guard Room README。

## 程式模組

| 檔案 | 責任 |
| --- | --- |
| [main.py](main.py) | App 組裝、生命週期與背景排程、graph／歷史查詢路由 |
| [graph_state.py](graph_state.py) | Config 驗證、JSONL 讀取、事件去重、指標與健康判定、live checkpoint |
| [graph_history.py](graph_history.py) | 原子 JSON 寫入、歷史保存與清理、索引、分頁及時間選取 |
| [logs.py](logs.py) | Console log 驗證、HTTP 接收、SSE log 廣播 |
| [frontend_api.py](frontend_api.py) | 其他前端路由與 mock 模式接線 |
| [frontend_live.py](frontend_live.py) | Monitor 與 SQLite 調查的唯讀前端投影 |
| [frontend_mock.py](frontend_mock.py) | 前端 mock 資料與記憶體狀態 |
| [investigation_api.py](investigation_api.py) | 調查 API、模型與 graph 來源接線、調查生命週期 |
| [investigation_store.py](investigation_store.py) | 調查 session 的 SQLite 持久化 |

## 調查開發設定

Investigation manager 預設將 session 存在 `../.data/investigations.sqlite3`。
可用 `NIGHTWATCH_INVESTIGATION_DB` 指定其他檔案，並以 `NIGHTWATCH_GRAPH_URL`
指定操作端設定的 graph 來源。

實際調查使用 `NIGHTWATCH_LLM_API_KEY`（或 `OPENAI_API_KEY`）與 `NIGHTWATCH_LLM_MODEL`。
離線檢查可透過 `install_investigations(..., model_factory=...)` 注入 Python model factory，
或在 app 啟動前替換 `app.state.investigation_manager.model_factory`。
HTTP client 不可選擇模型或 graph URL。完整介面見 [調查前端文件](../INVESTIGATION-FRONTEND.md)。

## SSE 接線

`/events` 只有一個 handler。啟用 `NIGHTWATCH_MOCK_DATA=1` 時，保留既有 state／journal
串流並送出 live log 事件；mock 關閉時，主程式提供真實 state、graph、調查 journal、
log 與 heartbeat。只有未安裝 live store 的獨立接線保留 log-only 模式。
兩種模式都驗證 cursor 語法。Log 僅即時傳遞，不參與 incident cursor replay；
graph watcher 與 investigation manager 都在組合後的 app lifespan 執行。
真實唯讀 API 已接入 monitor checkpoint 與 investigation SQLite；故障操作及完整實驗稽核仍未接入。
獨立調查報告與保存的 graph 證據見 `/api/investigations/{id}/report`、`/api/investigations/{id}/snapshots`。
其他前端 API 見 [前端 API 文件](../FRONTEND-API.md)。

## 文件分工

- [Guard Room README](../../guardroom/README.md)：資料流、topology config、p95 warning、更新頻率、snapshot／歷史 API 與操作限制。
- [Monitor README](../monitor/README.md)：Decorator、原始事件、logger 過濾與 JSONL／HTTP sink。
- [前端 API 文件](../FRONTEND-API.md)：其他前端介面的契約與操作範例。
- [Graph schema](../../console/schema-draft/graph.schema.json)：Graph 回傳格式及引用的正式 schema。

修改功能規則或設定時同步更新 Guard Room README；本文件維護開發入口與模組分工。
