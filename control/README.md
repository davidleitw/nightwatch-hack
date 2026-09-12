# Monitor 與調查 Agent

`monitor/` 提供 `@monitor` 與 JSONL／HTTP 輸出。`nightwatch_agent/` 用真實 Guard Room API 調查，`server/` 提供 HTTP、持久化調查與自動偵測。完整缺口見 [盤點](../INTEGRATION.md)。

## CLI

依賴安裝好後，在 repo 根目錄執行：

```sh
bash control/run-agent.sh --describe-context
bash control/run-agent.sh --model
bash control/run-agent.sh --graph-url http://127.0.0.1:9999/api/graph --model
```

`--describe-context` 只讀 graph 並列出 prompt／工具，不呼叫模型。`--model` 使用真實模型，需要模型服務可達與金鑰。腳本以 `uv run --locked --offline` 執行，依序選 `control/.env` 或根目錄 `.env`，不安裝新依賴。

| 變數 | 行為 |
| --- | --- |
| `NIGHTWATCH_GRAPH_URL` | 預設 `http://127.0.0.1:9999/api/graph`；CLI `--graph-url` 優先 |
| `NIGHTWATCH_LLM_API_KEY`、`OPENAI_API_KEY` | 前者優先 |
| `NIGHTWATCH_LLM_ENDPOINT` | 預設 `https://api.openai.com/v1/responses` |
| `NIGHTWATCH_LLM_MODEL` | 預設 `gpt-6-astra`；CLI `--model MODEL` 優先 |

模型或資料來源失敗會回報錯誤，不會自動換成錄影。

## 實際工具與限制

修復與 cache 設計見 [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md)。設定 `NIGHTWATCH_SHOP_URL` 為獨占操作的本機店面 HTTP origin，才提供 `get_demo_faults`、`deactivate_demo_fault`、`check_shop_health`；解除演練故障不等於業務恢復。

真實來源提供 `get_graph`、`list_graph_snapshots`、`get_node_detail`、`search_logs`，以 `submit_report` 交付結構化報告。報告記錄 findings、hypotheses、limitations、next_steps，驗證節點與 evidence ID；有調查報告不代表修復成功。

歷史工具讀原始快照，沒有可信基線。`search_logs` 只在最新一批保留日誌內搜尋，沒有全歷史分頁。Prometheus、trace 與通用 runtime 修復未接入；可明確啟用本機店面 demo-fault 解除及 health 查詢。

`alive` 是 monitor 窗口內有無事件；edge 來自設定，尚無邊流量量測；缺失值是 `null`。不要把沒有事件解讀成服務已死，或把 `null` 當零。

## 明確選用的離線資料

```sh
bash control/run-agent.sh --replay-model
bash control/run-agent.sh --fixture ../contracts/fixtures/catalog_pool_leak --model
```

`--replay-model` 使用腳本模型與錄影工具，完全離線；目前錄影缺少足夠 trace 證據，回 `unresolved`、exit 1。`--fixture --model` 使用真模型配錄影工具，不能算 live 資料驗證。

## HTTP 與驗證

啟動與端點見 [server/README.md](server/README.md)。HTTP server 使用程序環境中的模型設定；與 CLI 不同，它不自行載入 `.env`。

既有離線測試：

```sh
PYTHONPATH=control control/.venv/bin/python -m unittest discover -s control/tests -v
```

這些測試包含模型替身，通過不等於真實模型端到端驗證。Monitor 使用方式見 [monitor/README.md](monitor/README.md)。

## 排查案例記憶

HTTP 後端會在每次調查結案時，從既有報告與工具事件產生簡單案例，與結案報告在同一 SQLite transaction 保存；不增加模型呼叫或外部儲存。服務升級時也會補建既有結案案例。失敗、中斷與證據不足的排查同樣保存，保留原始狀態與限制，不當成已解決問題。

每次手動或自動觸發 agent，harness 在第一則 user message 的第一個欄位 `recent_investigations` 注入最近五次結案摘要（不足五次就全部，最新在前）。摘要最多 16 KiB，只帶案例 ID、時間、狀態、結論、症狀與候選根因；文字裁切明確標為 `preview_only`。模型認為可能重複時，以 `get_investigation_memory({"id":"inv-…"})` 讀取詳細案例；已知的更舊 ID 也可查。不存在或尚未結案的 ID 會回明確錯誤。查詢沿用既有工具預算，回覆上限 32 KiB，超量會標明 `truncated`。

案例格式 `nightwatch.investigation-memory.v1` 存在同一資料庫的 `investigation_memories` 表，ID 與原調查相同：

| 欄位 | 內容 |
| --- | --- |
| `schema_version`, `id`, `closed_at` | 版本、原調查 ID、結案時間 |
| `status`, `outcome`, `conclusion` | 執行狀態、結案結果與原報告結論；無有效報告時 conclusion 為 null |
| `trigger`, `data_scope` | 原觸發原因與觀測範圍；沒有範圍資訊時為 null |
| `summary_zh`, `symptoms` | 原摘要與 findings，保留 node_ids、evidence_ids |
| `investigation_steps` | 依順序保存工具、參數、觀測摘要、舊 evidence_id；失敗保留錯誤，未回傳標為 no_result |
| `root_cause.status` | 有 hypotheses 時為 candidate，沒有則 unknown；不自行升級為確認根因 |
| `root_cause.candidates` | 原 hypotheses：cause_zh、node_ids、supporting_evidence_ids、counterevidence_ids、uncertainty_zh |
| `limitations`, `next_steps` | 原限制與後續查證建議 |

模型仍先取得當前 graph，再對照舊候選根因的成立條件、反證、節點、錯誤與時間；須指出當下符合、不符及未知之處。舊案例是查證線索，不能證明事故重演，也不授權重做修復。報告驗證器會拒絕用案例查詢的 evidence ID 支持本次 findings 或 hypotheses；模型必須另外取得當次觀測證據。舊工具步驟不再複製歷史查詢結果，避免案例遞迴膨脹。

完整原始工具結果仍在原調查的 evidence/export；案例本身保存簡化查詢過程，不複製原始日誌或整段對話。資料隨既有 investigation DB 持久化，目前沒有另外的清除政策。本功能接在持久化 HTTP 後端；獨立 CLI 沿用原本單次調查模式。
