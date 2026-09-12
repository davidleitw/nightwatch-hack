# NightWatch

目前程式由購物網站、Monitor／Guard Room、調查 Agent 與觀測介面組成。實作與 mock 的盤點見 [INTEGRATION.md](INTEGRATION.md)。

| 目錄 | 現行用途 |
| --- | --- |
| [shop-web/](shop-web/README.md) | React 網站、FastAPI gateway、catalog、cart、order；SQLite 保存資料 |
| [control/](control/README.md) | Monitor 函式監測、讀取真實 graph／log 的調查 Agent |
| [control/server/](control/server/README.md) | Guard Room HTTP API、歷史快照、SQLite 調查、SSE |
| [guardroom/](guardroom/README.md) | Monitor 對應設定與本機啟動腳本 |
| [console/](console/README.md) | 真實調查工作區，以及明確選取的 mock／錄影介面 |
| `contracts/schemas/`、`contracts/fixtures/` | 程式與測試仍載入的 JSON schema、離線錄影；舊架構說明已移除 |
| `gate/` | PR／harness 工具，與網站執行流程分開 |

## 啟動

以下命令都從 repo 根目錄執行。需要預先安裝各目錄的依賴；沒有快取時，安裝與 Docker build 需要網路。

```sh
make -C shop-web up
bash guardroom/restart.sh
python3 console/build.py
python3 console/serve.py --port 4173 --control-url http://127.0.0.1:8001
```

網站預設為 `http://127.0.0.1:8080`，觀測介面為 `http://127.0.0.1:4173`，Guard Room 的 API 文件為 `http://127.0.0.1:8001/docs`。Guard Room 必須使用單一 worker；改埠時也要設定 `NIGHTWATCH_GRAPH_URL`。

真實模型調查需要 `NIGHTWATCH_LLM_API_KEY` 或 `OPENAI_API_KEY`。HTTP server 讀程序環境，不會自行載入 `.env`；CLI `control/run-agent.sh` 才會載入 dotenv。自動偵測也可能觸發模型呼叫。詳見 [control 啟動說明](control/README.md)。

目前可觀察、調查並保存報告，尚未串接自動修復。店面的示範結帳沒有金流或物流。

根目錄的 `task.sh`、`run-task.sh`、`setup.sh`、`hackathon.conf` 仍被既有 gate 工具引用，因此保留；其舊編號任務資料已移除，不是現行服務的啟動入口。
