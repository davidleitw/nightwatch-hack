# Python agent loop

本輪負責最小調查 loop，使用 **PydanticAI 2.43**。模型可以選工具、讀取帶編號的證據，
再決定下一個查詢，最後交出符合 `contracts/schemas/agent-report.schema.json` 的 JSON。
PydanticAI 管理對話與 function-call 往返；本專案只處理 NightWatch 的規則。

假設：依本次需求採 Python 與現成 framework，取代舊 `BRIEF.md` 的 Go 技術選擇；
工作位置為 `nightwatch-hack/control/`。這不是整個 control 服務，也不是 tasks/06 的完整驗收。

## 目錄與資料來源

| 位置 | 責任 |
| --- | --- |
| `../shop/` | 店面與故障注入介面，由另一個部份負責 |
| `../console/` | 前端，由另一個部份負責 |
| `../contracts/` | 唯讀的 API、工具、報告與事故錄影 |
| `nightwatch_agent/loop.py` | framework 接線、工具驗證、證據、預算與報告檢查 |
| `nightwatch_agent/replay.py` | 讀取既有事故錄影；未錄下的查詢明確失敗 |
| `nightwatch_agent/__main__.py` | CLI：離線重播或真模型查錄影 |
| `run-agent.sh` | 載入 `.env`、固定工作目錄與預設錄影路徑 |
| `.env.example` | 設定範例，不含真實金鑰 |
| `tests/test_loop.py` | 用 framework 的 FunctionModel 替身驗證程序與契約邊界 |

參考來源為 `~/Desktop/NightWatch/hackathon/docs/agent-capabilities-and-loop.md`、
該專案的 `control/internal/server/agent.go`，以及本 repo 的 `contracts/AGENT.md`、
`contracts/API.md` §8、`contracts/oteldemo/AGENT-CONTEXT.md`。

## NightWatch 有哪些 tools

| 工具 | 能查什麼 | 真資料應由誰提供 |
| --- | --- | --- |
| `get_node_history` | 節點的歷史與基線，找最早偏離 | control 的快照 |
| `get_node_detail` | 當前量測、資源、進出邊 | control 的 graph |
| `find_traces` | 錯誤／慢請求的 trace ID | Jaeger |
| `get_trace` | 一筆請求的錯誤路徑與被吸收的錯誤 | Jaeger |
| `search_logs` | 錯誤日誌與重複模式 | control logstore |
| `query_metric` | 指標值、正常值與差值 | Prometheus／control |
| `run_health_check` | 宣告的探針是否成功 | manifest check URL |
| `inspect_runtime` | 現在設定與可調整項目 | runtime adapter＋manifest |
| `list_errors` | 多節點的錯誤先後順序 | 選配的 Guard Room |
| `get_node_errors` | 某節點最新錯誤原文 | 選配的 Guard Room |

舊版一般啟用前八個，Guard Room 啟用時共十個；沒有 runtime target 時會省略
`inspect_runtime`。這些是內部 function tools，**不是十條同名 HTTP API**。

本 loop 接受 `capabilities.tools` 的既有名稱與 JSON schema，僅允許上述唯讀工具。
資料實作透過 `backend(name, args)` 接入；未接上的工具就不要放進 capabilities。
隨附錄影只包含 history、find_traces、get_trace、detail 四個，所以 CLI 只掛這四個。
尚未提供 live Prometheus／Jaeger／runtime adapter；沒有把錄影當成 live fallback。

## 跑起來

從 `control/` 執行，需 Python 3.11 以上；本機已用 Python 3.14.7 跑過。

```sh
uv sync --locked
bash run-agent.sh --replay-model
```

`--replay-model` 只重播錄下的模型呼叫，不需要金鑰或對外連線。stderr 每行輸出工具／證據
進度，stdout 最後一行是結果。這份舊錄影的 `get_trace.path` 為空，因此預期結果是
`unresolved`、`procedure_incomplete`、四筆證據、exit code 1。它證明工具往返可以執行且
不完整的證據會被拒絕，不能用它宣稱模型已找到根因。

讓真模型自主選擇錄影中的查詢：

```sh
bash run-agent.sh --model
```

腳本優先載入 `control/.env`，沒有才載入 repo 根目錄的 `.env`；兩份都沒有則使用程序
原本的環境變數。由 uv 解析檔案，不使用 shell `source`，既有程序環境變數優先。
目前提供的 `.env` 在 repo 根目錄，包含 `OPENAI_API_KEY`，可以直接用上面的指令。
需要建立 control 專用設定時，欄位見 `.env.example`。

| 設定 | 用途與優先序 |
| --- | --- |
| `NIGHTWATCH_LLM_API_KEY`／`OPENAI_API_KEY` | 金鑰，前者優先；不放進 prompt 或日誌 |
| `NIGHTWATCH_LLM_MODEL` | 預設 `gpt-5.6-luna`；`--model MODEL` 可覆寫 |
| `NIGHTWATCH_LLM_ENDPOINT` | 完整 Responses URL，預設 `https://api.openai.com/v1/responses` |

這裡的模型名稱沿用舊 control 設定，需帳號實際可使用的模型。`uv --offline` 只禁止 uv
下載套件，**不會禁止真模型的 API 連線**。自訂 Responses endpoint 用
`NIGHTWATCH_LLM_ENDPOINT`，預設 `https://api.openai.com/v1/responses`。
沒有金鑰或 API 失敗會明確失敗，不切換假模型。這個模式仍使用錄影工具，不代表 live 調查。

腳本可以從任意目錄執行，亦可用 `--fixture PATH`、`--report-schema PATH` 覆寫預設。
繞過腳本直接使用 CLI 時，需自行把 `.env` 載入，例如
`uv run --env-file ../.env nightwatch-agent --fixture ../contracts/fixtures/catalog_pool_leak --model`。
`control/.gitignore` 已排除 control 底下的 `.env`；根目錄的 `.env` 仍須由 repo 維護者
在根目錄 `.gitignore` 排除，本輪遵守只改 `control/` 的分工。

## 接入其他 control 程式

呼叫 `await investigate(model=..., capabilities=..., opening=..., report_schema=..., backend=...)`。
`model` 可以是 PydanticAI Model instance；`capabilities` 提供 `nodes` 與 `tools`，每個 tool
包含 `name`、`summary_zh`、`parameters`。報告 schema 直接讀共用契約，不複製修改。

`backend` 是 async 函式，參數為工具名稱與已驗證的參數 dict，回傳：

```python
Observation(
    result=tool_result,       # 工具契約規定的 JSON dict
    source="jaeger",          # 實際來源
    t=12,                     # 相對本次事故偵測時間的秒數
    summary_zh="已讀取請求路徑。",
)
```

由資料提供者負責時間換算、history 的基線、trace 攤平、log 聚合及各工具回傳形狀。
錯誤可 raise `ValueError`、`LookupError`、`OSError`；loop 回給模型並保留失敗事件。
`opening` 只放偵測摘要與觀測資料，不要傳整份 `/api/state`：其中有故障卡與舊結論。
`on_event` 可接 UI／journal；目前事件是程序內回呼，尚非公開 SSE 或 incident-commit 契約。

每次呼叫 `investigate` 都有獨立的對話、工具預算和證據集合。回傳 `LoopResult` 包含
`report`、`evidence`、`events`、`messages`、`usage`，狀態有：

- `report_ready`：格式、必要查證與引用有效；仍須下游做根因／修復稽核。
- `unresolved`：證據不足、報告無效或模型不可用。
- `budget_exhausted`：時間、tokens 或模型請求上限已用完。

## 這版的限制

預設 20 次工具、900 秒、400,000 tokens；每個資料查詢 10 秒逾時，模型請求 timeout
設定 60 秒。工具參數錯誤也計次。20 次用完會移除工具，讓模型交最後報告；時間或
token 用完則直接停止。token 用量是供應商回覆後才知道，因此最後一則回覆可能超過額度。
CLI 的模型 SDK 對可重試連線失敗最多 retry 兩次，單次 SDK 呼叫可能超過 60 秒；
整次調查仍有 900 秒硬上限。尚未做原 Go 版的逐輪 model.turn_failed journal。

報告格式錯或引用不合法可重交一次；沒有非空 history／授權 trace 則直接 unresolved。
證據只保留實際給模型的內容，超大結果縮小並標示 `truncated`，空內容不算完成程序。
靜態 instructions 不含事故時間或 ID；後續模型訊息由 framework 累積，工具結果帶剩餘預算。
尚未串接 live 工具資料、偵測、公開 API、SSE、六項稽核、批准、修復與恢復驗證。

## 驗證

```sh
PYDANTIC_AI_NO_BANNER=1 uv run --offline python -m unittest discover -s tests -v
uv build --offline
```

測試包含：模型多輪工具往返、參數上下界、trace 授權、空路徑拒絕、格式修正一次、
不存在的證據引用、預算與 timeout、穩定前綴、schema 範例與 JSON 截斷。
成功報告案例的 trace 是明確的測試替身，不是補造到正式錄影的資料。

2026-09-12 已從 repo 根目錄 `.env` 載入金鑰，實際執行 `bash run-agent.sh --model`，
使用 `gpt-5.6-luna` 自主查詢錄影：7 次模型請求、11 次工具呼叫、4 筆有效回傳證據，
其餘查詢因錄影未收錄而明確失敗。framework 回報 input 30,868 tokens、output 1,739、
cache read 25,428。最終因 `get_trace.path` 為空而回 `unresolved / procedure_incomplete`。
這次已驗證真模型 API、工具往返、cache 用量回報與程序檢查；未驗證 live 資料來源或
根因判斷成功。既有 11 項測試與離線套件 build 亦通過。

Framework 介面依據：[PydanticAI tools](https://pydantic.dev/docs/ai/api/pydantic-ai/tools/)、
[output validators](https://pydantic.dev/docs/ai/core-concepts/output/)。
`Tool.from_schema` 不自行驗參數，因此另用 JSON Schema validator 執行契約檢查。
