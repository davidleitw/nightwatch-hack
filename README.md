# NightWatch

目前程式由購物網站、Monitor／Guard Room、調查 Agent 與觀測介面組成。實作與 mock 的盤點見 [INTEGRATION.md](INTEGRATION.md)。

| 目錄 | 現行用途 |
| --- | --- |
| [shop-web/](shop-web/README.md) | React 網站、FastAPI gateway、catalog、cart、order；SQLite 保存資料 |
| [control/](control/README.md) | Monitor 函式監測、讀取真實 graph／log 的調查 Agent |
| [control/server/](control/server/README.md) | Guard Room HTTP API、歷史快照、SQLite 調查、SSE |
| [guardroom/](guardroom/README.md) | Docker Compose 部署、Monitor 對應設定與啟動腳本 |
| [console/](console/README.md) | 真實調查工作區，以及明確選取的 mock／錄影介面 |
| `contracts/schemas/`、`contracts/fixtures/` | 程式與測試仍載入的 JSON schema、離線錄影；舊架構說明已移除 |
| `gate/` | PR／harness 工具，與網站執行流程分開 |

## 一鍵重啟全部服務

需要 Docker Engine／Desktop、Compose v2、Python 3、curl、lsof。從 repo root 執行：

```sh
./restart.sh          # build + restart Shop、Guard Room、Console
./restart.sh --open   # Docker 使用現有映像；重新建置並啟動 Console
./restart.sh --close  # 停止全部服務，保留資料
```

Shop／Guard Room 使用各自的 Compose project；Console 在 host 背景執行，
PID 與 log 位於 Git 忽略的 `.run/console.pid`、`.run/console.log`。
腳本可從其他工作目錄以絕對路徑執行；遇到非本 checkout 的 Console 占用 port 時會拒絕停止它。
重啟會短暫中斷連線，進行中的 AI 調查可能被標記 interrupted，請待調查結束再操作。
不會刪除 Docker volume 或 monitor log。腳本不直接呼叫模型；若已設定 API key，
Guard Room 啟動後偵測到異常時，仍可能自動啟動 AI 調查並產生模型費用。

預設沿用已部署的 Shop／Guard Room host port；首次部署 Shop 為 8080／8000，
Guard Room 為 9999，Console 為 4173。可透過 `FRONTEND_PORT`、`BACKEND_PORT`、
`PORT`（Guard Room）、`CONSOLE_PORT` 明確覆寫，例如：

```sh
FRONTEND_PORT=8081 BACKEND_PORT=8001 PORT=9999 CONSOLE_PORT=4173 ./restart.sh
```

若只在 `guardroom/.env` 修改 PORT，已存在的容器仍優先沿用舊 port；請用上述 `PORT=...` 覆寫。
Console 的 upstream 自動指向本次啟動的 Guard Room，不使用 mock。
如需 AI 調查，在 Git 忽略的 `guardroom/.env` 設定 `OPENAI_API_KEY`；
腳本只顯示有無設定，不驗證金鑰或呼叫模型。Monitor／graph 功能不需要 key。

以目前使用的 8081／8001／9999／4173 為例，頁面如下；腳本成功後會列印實際網址：

| 頁面 | 網址 |
| --- | --- |
| 購物網站 | http://127.0.0.1:8081/ |
| Shop 故障演練 | http://127.0.0.1:8081/#/events |
| 即時拓樸與 AI 調查 | http://127.0.0.1:4173/?source=live#topology |
| 調查歷史 | http://127.0.0.1:4173/?source=live#investigations |
| Shop API 文件 | http://127.0.0.1:8001/docs |
| Guard Room API 文件 | http://127.0.0.1:9999/docs |
| 最新 graph JSON | http://127.0.0.1:9999/api/graph |

故障演練由 checkout request、checkout logic、DB write 三層 monitor 觀測：
延遲會讓 request 顯示 warning；結帳例外影響 request／logic；DB 鎖定會影響三層。
需在啟用故障後實際送出結帳請求才會產生觀測；窗口內混有成功資料時，失敗節點顯示 warning。
判定與門檻見 [Guard Room 說明](guardroom/README.md#結帳故障觀測)。

個別元件的啟動方式見上表。HTTP server 本身不載入 dotenv；Docker Compose 從 `guardroom/.env` 取得並注入設定，CLI 則讀 `control/.env` 或根目錄 `.env`。自動偵測可能觸發模型呼叫。

目前可觀察、調查並保存報告；本機 CLI／host server 可明確啟用解除演練故障的工具，解除故障不等於業務恢復。Docker 部署預設未啟用修復工具。店面的示範結帳沒有金流或物流。

根目錄的 `task.sh`、`run-task.sh`、`setup.sh`、`hackathon.conf` 仍被既有 gate 工具引用，因此保留；其舊編號任務資料已移除，不是現行服務的啟動入口。
