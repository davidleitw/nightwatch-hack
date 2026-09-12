# NightWatch hackathon

目前的購物網站與 Python 調查 loop 分別在 `shop-web/`、`control/`；各自的啟動方式與限制見下表。

| 路徑 | 用途 |
| --- | --- |
| [shop-web/](shop-web/README.md) | React + FastAPI 購物網站，使用 Docker Compose 啟動 |
| [control/](control/README.md) | Python agent 調查 loop；目前使用事故錄影，尚未接上 live 工具 |
| [BACKEND.md](BACKEND.md) | Monitor、Guard Room、Watcher 的專案方向；根目錄保留新加入的 Python 專案骨架 |
| [contracts/](contracts/README.md) | 共用契約、schema 與事故錄影，維持原路徑 |
| [gate/](gate/SKILL.md) | PR 檢查與合併工具；有寫入操作，使用前依該目錄說明確認授權 |
| [archive/](archive/README.md) | 舊 shop、console 任務、control 的 Go 任務及模擬服務，保留原文供查閱與還原 |

## 執行現有程式

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
