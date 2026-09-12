# NightWatch hackathon

購物網站、Guard Room 與監控工作台可從 repo root 一鍵啟動；各部分說明見下表。

| 路徑 | 用途 |
| --- | --- |
| [shop-web/](shop-web/README.md) | React + FastAPI 購物網站，使用 Docker Compose 啟動 |
| [guardroom/](guardroom/README.md) | Docker 部署、monitor graph、歷史 snapshot 與調查 API；實作在 `control/server/` |
| [console/](console/README.md) | 即時拓樸、monitor log 與 AI 調查工作台 |
| [control/](control/README.md) | Monitor 套件與 Python 調查引擎；可讀 graph API 或使用事故錄影 |
| [BACKEND.md](BACKEND.md) | Monitor、Guard Room、Watcher 的專案方向；根目錄保留新加入的 Python 專案骨架 |
| [contracts/](contracts/README.md) | 共用契約、schema 與事故錄影，維持原路徑 |
| [gate/](gate/SKILL.md) | PR 檢查與合併工具；有寫入操作，使用前依該目錄說明確認授權 |
| [archive/](archive/README.md) | 舊 shop、console 任務、control 的 Go 任務及模擬服務，保留原文供查閱與還原 |

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

## 單獨執行現有程式

購物網站：

```sh
cd shop-web
make up
```

前端預設位於 `http://localhost:8080/`。建置需求、資料保存與停止方式見 [shop-web README](shop-web/README.md)。

Python 調查 loop：

```sh
cd control
uv sync --locked
bash run-agent.sh --replay-model
```

安裝好依賴後，`--replay-model` 不需要模型金鑰或對外連線。既有錄影證據不足時會明確回報
`unresolved` 並以 exit code 1 結束；這不是 live 調查成功，詳見 [control README](control/README.md)。

## 舊任務流程

原本 `shop/`、`control/`、`console/` 三部份的編號任務已移至 `archive/legacy-hackathon/`。
舊版開工說明完整保留於 [歷史 README](archive/legacy-hackathon/README.md)。

根目錄的 `setup.sh`、`task.sh`、`run-task.sh`、`TASK-TEMPLATE.md` 與 `hackathon.conf` 仍保留；
其中依賴 `BRIEF.md`、`tasks/`、`stubs/` 的舊編號任務流程，必須先依
[封存說明](archive/README.md) 還原目錄才能使用。請以現有程式各自的 README 作為啟動入口。
