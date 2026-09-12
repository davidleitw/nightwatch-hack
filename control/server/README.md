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
| [detector.py](detector.py) | 新快照的連續異常與恢復判定；持久旗標由 investigation store 保存 |

## 調查開發設定

Investigation manager 預設將 session 存在 `../.data/investigations.sqlite3`。
可用 `NIGHTWATCH_INVESTIGATION_DB` 指定其他檔案，並以 `NIGHTWATCH_GRAPH_URL`
指定操作端設定的 graph 來源。

實際調查使用 `NIGHTWATCH_LLM_API_KEY`（或 `OPENAI_API_KEY`）與 `NIGHTWATCH_LLM_MODEL`（預設 `gpt-6-astra`）。
Live graph adapter 提供 `get_graph`（含歷史 timestamp）、`list_graph_snapshots`
、`get_node_detail`、`search_logs` 與結案工具 `submit_report`，詳見 [agent tools](../README.md)。更新 agent package 後需重啟後端，
讓新調查載入新的 tools 與 prompt。
離線檢查可透過 `install_investigations(..., model_factory=...)` 注入 Python model factory，
或在 app 啟動前替換 `app.state.investigation_manager.model_factory`。
HTTP client 不可選擇模型或 graph URL。完整介面見 [調查前端文件](../INVESTIGATION-FRONTEND.md)。

## 自動偵測與調查旗標

Investigation manager 在既有約每 5 秒的 graph refresh 後執行偵測，與瀏覽器是否連線無關。
同一節點連續三次新快照為 `warning` 或 `failing` 才確認異常；中途兩種狀態互換仍算異常。
直接沿用 Guard Room 的 status，不自行重算錯誤／延遲門檻，也不把 `alive=false` 當作死亡。
`unknown` 不觸發。這是目前 Monitor graph 的規則，不是舊 shopper／基線偵測規則。

只接受 seq 與 at 都前進、快照距現在不超過 15 秒（容許未來 5 秒時鐘差）、
且 `sources.logstore.ok=true` 的資料。兩張快照間隔超過 15 秒會重新累計；讀取失敗、
重複／倒退快照或來源無新資料也會中斷累計。Prometheus／Jaeger 未接入不阻擋此偵測。
Graph URL 帶 query（預覽／指定歷史）或 `NIGHTWATCH_MOCK_DATA=1` 時停用自動偵測。

確認後先在同一個 investigation SQLite 的 `detections` 表保存 `latched=1`、固定 request_id、
觸發節點、三次確認量測與當時完整 graph，再透過既有 manager 建立 `trigger.source=detector`
的 session。每個來源 URL 只有一個有效旗標，旗標生效期間其他異常不另開調查。
Agent 開場會收到保存的觸發資訊，再用現有唯讀工具取證；偵測本身不是根因結論或工具 evidence ID。

旗標生命週期：

- 同一異常持續存在時，session 完成、unresolved、模型失敗或服務重啟都不清旗標、不自動重跑。
- 若已有手動調查，保存這一次偵測，等待執行名額；每次讀到新 graph 都重新確認觸發節點
  仍有異常才嘗試建立。這不新增調查佇列，同時仍只有一個 running session。
- 若程序在寫入旗標後、建立 session 前停止，重啟後沿用同一 request_id 嘗試建立。
  若 session 已存在，就不重跑；原本 running 的 session 依既有機制歸檔為 interrupted。
- 觸發節點都連續三次新快照為 `ok`、全圖沒有 warning／failing，且沒有 running session，
  才把旗標改為 `latched=0`。觸發節點 unknown／消失不算恢復。解除後重新累計下一次異常。
- 偵測資料會隨 session 歷史保留。程序重啟只重置尚未確認的累計，不解除已保存旗標。
  偵測／保存錯誤會記錄 backend error log，下次 refresh 重試，不影響 graph 更新。

三次確認、15 秒新鮮度與恢復後重新啟用，是本次自動排查採用的預設假設。
自動排查會使用既有模型設定；未設定金鑰仍保存 failed session，不會因每次 polling 重複失敗開案。
新表與唯一索引在開啟既有 DB 時建立，不改既有 session、事件或 HTTP response 欄位。

## SSE 接線

`/events` 只有一個 handler。啟用 `NIGHTWATCH_MOCK_DATA=1` 時，保留既有 state／journal
串流並送出 live log 事件；mock 關閉時，主程式提供真實 state、graph、調查 journal、
log 與 heartbeat。只有未安裝 live store 的獨立接線保留 log-only 模式。
兩種模式都驗證 cursor 語法。Log 僅即時傳遞，不參與 incident cursor replay；
graph watcher 與 investigation manager 都在組合後的 app lifespan 執行。
真實唯讀 API 已接入 monitor checkpoint 與 investigation SQLite；故障操作及完整實驗稽核仍未接入。
獨立調查報告與保存的 graph 證據見 `/api/investigations/{id}/report`、`/api/investigations/{id}/snapshots`。
完整歸檔使用 `/api/investigations/{id}/export`；report/context 欄位見 [Agent 報告 API](../AGENT-REPORT-API.md)。
其他前端 API 見 [前端 API 文件](../FRONTEND-API.md)。

## 文件分工

- [Guard Room README](../../guardroom/README.md)：資料流、topology config、p95 warning、更新頻率、snapshot／歷史 API 與操作限制。
- [Monitor README](../monitor/README.md)：Decorator、原始事件、logger 過濾與 JSONL／HTTP sink。
- [前端 API 文件](../FRONTEND-API.md)：其他前端介面的契約與操作範例。
- [Graph schema](../../console/schema-draft/graph.schema.json)：Graph 回傳格式及引用的正式 schema。

修改功能規則或設定時同步更新 Guard Room README；本文件維護開發入口與模組分工。
