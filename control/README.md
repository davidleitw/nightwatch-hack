# Guard Room agent loop

使用 PydanticAI 的 function-call loop，預設模型為 `gpt-6-astra`。
CLI 與現有 FastAPI investigation manager 使用同一個 Guard Room adapter。
模型自主選擇查詢，每次成功結果編成 `ev-0001` 等證據，最後保存調查結果。

## 這輪實際提供的工具

依據 [Guard Room README](../guardroom/README.md) 的 HTTP API：

| 工具與參數 | 實際資料來源 | 排查用途 |
| --- | --- | --- |
| `get_graph({timestamp?})` | `GET /api/graph` 或 `GET /api/graph?timestamp=...` | 先看全圖，再比較指定時間的節點量測與依賴關係 |
| `list_graph_snapshots({limit?, before_seq?})` | `GET /api/graph/snapshots` | 找到有保存資料的時間；取回的 `at` 可交給 `get_graph` |
| `get_node_detail({node})` | `GET /api/graph`，投影指定節點及進出邊 | 聚焦候選節點的當前量測、資源欄位及依賴；不是另一條 HTTP endpoint |
| `search_logs({node, severity?, query?, since?, until?, limit?, fetch_limit?})` | `GET /api/debug/logs?service=...&limit=...` | 搜尋近期保留日誌，依文字、嚴重度與時間篩選 |
| `submit_report(...)` | PydanticAI output tool；不是 HTTP 寫入工具 | 提交結構化調查結果，後端自動歸檔完整 context |

`limit` 為 1–500（預設 100）。歷史索引保留 API 的 `snapshots`、`seq`、`at`、
`next_before_seq` 欄位，下一頁用 `before_seq=next_before_seq`。時間必須含時區，
URL 編碼由 adapter 負責。查詢回傳不晚於指定時間的最後一份快照，判讀使用回傳的
`at` 與 `seq`。沒有保留資料回空清單；HTTP 404／連線失敗會明確回工具錯誤。

工具會驗證參數、graph 與歷史索引；移除既有 `assessment`，不把別人的判斷當觀測。
缺失量測保留 `null`。完整 graph 上限 16 KiB、索引上限 64 KiB；超過上限明確拒絕，
不刪掉拓撲或分頁資料。單次 HTTP timeout 5 秒、回應上限 512 KiB，工具共用 10 秒 timeout。

**沒有接上的工具不會提供給模型。** 舊程式還保留錄影／其他 adapter 的工具名稱，
但 live Guard Room 不提供 `find_traces`、`get_trace`、`query_metric`、
`run_health_check`、`inspect_runtime`、`list_errors`、`get_node_errors`。
README 的 `POST /api/logs` 是寫入，`GET /events` 是無歷史回放的 live SSE，
兩者都不是供排查用的歷史錯誤查詢 API。

沒有以 graph 假造舊 `get_node_history`：舊 history 契約需要基線與完整數字序列，
而 Guard Room 提供的是含 `null` 的原始快照。歷史比較直接用原始快照。

## Prompt 的排查方式

1. 讀目前 graph，判讀來源新鮮度、哪些節點確實有完成呼叫及哪些量測缺失。
2. 查歷史索引，選幾個有用的前後時間，讀取 graph 比較候選節點與呼叫端的變化。
3. 有需要時讀節點詳情，整理候選原因、反證、可觀測的變化時間範圍與缺少的證據。
4. 用 `search_logs` 查候選節點的近期日誌，辨識錯誤文字與反證。
5. 以實際 evidence ID 呼叫 `submit_report`；證據不足也提交結構化報告。

Prompt 特別區分 Guard Room 的量測語意：`alive=false` 表示窗口內沒事件，不等於
服務死亡；edge 是 config 宣告且尚無量測；Prometheus／Jaeger 尚未整合；
`null` 不等於零；巢狀 monitor 次數不等於受影響顧客數。完整文字與工具描述在
[nightwatch_agent/prompts.py](nightwatch_agent/prompts.py)。

Graph 調查使用獨立的 `submit_report` 格式，不再強制要求舊 history／trace。
報告列出 findings、hypotheses、limitations、next_steps；`conclusion` 為 supported 或 inconclusive。
仍檢查節點與證據 ID，supported 必須有引用證據的 finding，inconclusive 必須說明限制。
兩者都有結構化報告，HTTP outcome 分別為 report_ready／unresolved，CLI 都以 exit 0 結束；
没有交出有效報告、逾時或執行失敗仍 exit 1。這不代表服務修復。
完整欄位、context 與 export API 見 [AGENT-REPORT-API.md](AGENT-REPORT-API.md)。

`search_logs` 預設抓最新 500 筆、最多 2000 筆，再於該批內篩選；預設回 20 筆、最多 50 筆。
時間需含時區，query 為不分大小寫的文字包含比對；WARNING 與 WARN 視為同級。
來源 checkpoint 全部節點合計最多保留 10000 個事件，不提供日誌分頁或完整歷史保證。
結果包含 scanned_count、matched_count_in_batch、returned_count、scanned_from/to、
api_limit_reached 與 truncated；沒有符合項目不表示沒有故障。
日誌最多回傳約 15 KiB 的完整記錄，過大時移除較舊匹配並標示截斷；單筆過大則回工具錯誤。

## 執行

先由服務擁有者啟動提供歷史 API 的 Guard Room，從 repo 根目錄執行：

```sh
bash control/run-agent.sh --describe-context
bash control/run-agent.sh --model
# 指定來源或明確覆寫既有環境中的模型：
bash control/run-agent.sh --graph-url http://127.0.0.1:8001/api/graph --model gpt-6-astra
```

`--describe-context` 只讀本機 API 並列印模型實際會收到的 prompt／工具／開場。
`--model` 才呼叫模型 API，**不是離線模式**。預設資料來源已改成 Guard Room，
不再默默用舊錄影。HTTP 端可用既有 `POST /api/investigations` 啟動，
介面見 [INVESTIGATION-FRONTEND.md](INVESTIGATION-FRONTEND.md)。

| 環境變數 | 預設與優先序 |
| --- | --- |
| `NIGHTWATCH_GRAPH_URL` | `http://127.0.0.1:8001/api/graph`；CLI `--graph-url` 優先 |
| `NIGHTWATCH_LLM_MODEL` | `gpt-6-astra`；CLI `--model MODEL` 優先 |
| `NIGHTWATCH_LLM_API_KEY`／`OPENAI_API_KEY` | 前者優先；不寫入 prompt 或日誌 |
| `NIGHTWATCH_LLM_ENDPOINT` | `https://api.openai.com/v1/responses` |

腳本以 uv 載入 `control/.env`，沒有才用根目錄 `.env`；程序已設的環境變數優先。
範例見 [.env.example](.env.example)。模型不可用會明確失敗，不換模型或回傳假答案。
Astra 的模型 ID 與 Responses tool calling 依據
[OpenAI 模型指南](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6-astra)。

若 8001 的 `/api/graph` 有回應但 `/api/graph/snapshots` 回 404，需更新／重啟該服務，
使其載入目前 `control/server/main.py`。只修改磁碟上的 agent 程式不會更新已啟動的程序。

## 明確選擇錄影／預覽

```sh
bash control/run-agent.sh --replay-model
bash control/run-agent.sh --fixture ../contracts/fixtures/catalog_pool_leak --model gpt-6-astra
bash control/run-agent.sh --graph-url 'http://127.0.0.1:8001/api/graph?state=problem' --describe-context
```

錄影模式只提供錄下的 history、detail、find_traces、get_trace 四個工具；
未錄下的查詢失敗。`--replay-model` 不連外，既有錄影因 trace path 為空回 unresolved。
帶 operator query 的 graph URL（demo／固定歷史）提供 `get_graph({})` 與 `submit_report`，
不與 live 歷史混用。假設：不帶 query 的 Guard Room URL 使用 README 的 monitor API。

## 開發與驗證

不新增套件。Python 3.11+，使用已鎖定的依賴；初次安裝在 `control/` 執行
`uv sync --locked`。既有依賴與 build backend 均需在本機可用才能離線 build。

```sh
cd control
PYDANTIC_AI_NO_BANNER=1 .venv/bin/python -m unittest discover -s tests -v
uv build --offline --python .venv/bin/python
```

一次調查的預算仍為 20 次工具、900 秒、400,000 tokens。工具錯誤計次；
回傳過大的節點詳情會加 `truncated`。每輪有獨立的對話與證據，不沿用舊結論。

本次工具／報告改動依使用者指示未執行測試、build、review、CLI 或真模型 API 驗證。
