# Monitor 與調查 Agent

`monitor/` 提供 `@monitor` 與 JSONL／HTTP 輸出。`nightwatch_agent/` 用真實 Guard Room API 調查，`server/` 提供 HTTP、持久化調查與自動偵測。完整缺口見 [盤點](../INTEGRATION.md)。

## CLI

依賴安裝好後，在 repo 根目錄執行：

```sh
bash control/run-agent.sh --describe-context
bash control/run-agent.sh --model
bash control/run-agent.sh --graph-url http://127.0.0.1:8001/api/graph --model
```

`--describe-context` 只讀 graph 並列出 prompt／工具，不呼叫模型。`--model` 使用真實模型，需要模型服務可達與金鑰。腳本以 `uv run --locked --offline` 執行，依序選 `control/.env` 或根目錄 `.env`，不安裝新依賴。

| 變數 | 行為 |
| --- | --- |
| `NIGHTWATCH_GRAPH_URL` | 預設 `http://127.0.0.1:8001/api/graph`；CLI `--graph-url` 優先 |
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
