# 日日選物（shop-web）

日日選物是本機購物 demo：前端使用 React 與 Vite，後端使用 FastAPI，Docker Compose 啟動 `frontend` 與 `backend` 兩個服務。前端容器內的 Nginx 提供頁面並將 `/api/` 請求代理到後端，訂單資料保存於 SQLite named volume。

## 專案結構

| 路徑 | 用途 |
| --- | --- |
| `frontend/` | React + Vite 前端；建置後由 Nginx 提供頁面 |
| `backend/` | FastAPI 應用程式、SQLite 存取、logging 與 API 整合測試 |
| `compose.yaml` | 定義 `frontend`、`backend` 服務與 `shop-data`、`shop-logs` named volumes |
| `Makefile` | Docker Compose 建置、啟停、檢查與測試入口 |

## 快速啟動

需求：已安裝 Docker（含 Compose v2）、GNU Make，以及執行 smoke check 所需的 `curl`。

```sh
cd shop-web
make up
```

本專案測試與 demo 使用固定的本機連接埠：

- 商店前端：<http://localhost:8080/>
- FastAPI Swagger UI：<http://localhost:8000/docs>
- 經 Nginx 代理的健康檢查：<http://localhost:8080/api/health>

Compose 預設 project name 是 `nightwatch-shop-web`，因此訂單 volume 的實際名稱為 `nightwatch-shop-web_shop-data`。`make down` 與 `make stop` 會保留資料；`make clean CONFIRM=yes` 會清除該 project 的 containers、網路、訂單資料與 application logs。

## Makefile 指令

```sh
./restart.sh          # 重建並重啟
./restart.sh --close  # 停止服務，保留容器與資料
./restart.sh --open   # 啟動服務，不重新 build（需已有 images）
```

執行 `./restart.sh` 可重建並重啟前後端、保留訂單資料，等待健康檢查並驗證 API。腳本可從任意工作目錄呼叫，預設沿用現有容器的 ports；尚未啟動時使用前端 8080、後端 8000。可用 `FRONTEND_PORT`、`BACKEND_PORT`、`PROJECT_NAME` 環境變數覆寫。失敗時輸出容器狀態與最近日誌。

Makefile 預設使用 `docker compose`，並在每個 Compose 指令加入 `-p $(PROJECT_NAME)`；預設值是 `PROJECT_NAME=nightwatch-shop-web`，設定來源都是 `compose.yaml`。也可以用 `COMPOSE=docker-compose` 指定舊版獨立 Compose CLI。下表的 Docker 指令以預設 project name 展開；若覆寫 `PROJECT_NAME`，請將 `nightwatch-shop-web` 換成指定的值。

| 指令 | 用途 | 等價 Docker Compose 操作 |
| --- | --- | --- |
| `make help` | 顯示指令用途；直接執行 `make` 也會顯示 | — |
| `make build` | 建置前後端 Docker images | `docker compose -p nightwatch-shop-web -f compose.yaml build` |
| `make rebuild` | 不使用快取重新建置 images | `docker compose -p nightwatch-shop-web -f compose.yaml build --no-cache` |
| `make up` | 建置並在背景啟動兩個 containers | `docker compose -p nightwatch-shop-web -f compose.yaml up -d --build` |
| `make down` | 移除 containers 與網路，保留資料 volumes | `docker compose -p nightwatch-shop-web -f compose.yaml down` |
| `make stop` | 停止 containers，保留 containers 與資料 | `docker compose -p nightwatch-shop-web -f compose.yaml stop` |
| `make restart` | 重新啟動現有 containers | `docker compose -p nightwatch-shop-web -f compose.yaml restart` |
| `make logs` | 持續查看前後端 log；按 `Ctrl-C` 離開 | `docker compose -p nightwatch-shop-web -f compose.yaml logs -f --tail=100` |
| `make ps` | 查看 containers 狀態 | `docker compose -p nightwatch-shop-web -f compose.yaml ps` |
| `make config` | 驗證並顯示 Compose 設定 | `docker compose -p nightwatch-shop-web -f compose.yaml config` |
| `make test` | 在已啟動的 backend container 執行 API 整合測試 | `docker compose -p nightwatch-shop-web -f compose.yaml exec -T backend python -m unittest discover -s tests -v` |
| `make smoke` | 檢查前端首頁與經 Nginx 代理的 API | `curl` 呼叫 `http://localhost:8080/` 與 `/api/health` |
| `make clean CONFIRM=yes` | 移除 containers、網路、訂單資料與 application logs | `docker compose -p nightwatch-shop-web -f compose.yaml down --volumes --remove-orphans` |

`make test` 必須先執行 `make up`，並會建立測試訂單。程式或 image 變更後請重新執行 `make up` 以建置並啟動；`make restart` 只會重啟現有 containers。

若舊版獨立 Compose 只回傳 `make test` 的 exit code 而沒有顯示測試明細，可直接查看 backend container：

```sh
docker exec nightwatch-shop-web_backend_1 python -m unittest discover -s tests -v
```

## 使用流程

首頁透過 `GET /api/products` 載入六項初始商品，可依分類篩選或以關鍵字搜尋。前端目前以瀏覽器 `localStorage` 保存購物袋，數量上限為每項 99 件；送出結帳時使用既有的 `POST /api/orders`。

後端另提供匿名購物車 CRUD 與購物車結帳 API，但前端尚未串接這些 cart API，完整 endpoint、request/response 欄位與錯誤狀態碼請見 [docs/API.md](docs/API.md)。這是本機購物 demo，不串接金流、扣款或出貨，請勿填寫真實個人資料。

## API 摘要

- 商品：`GET/POST /api/products`、`GET/PUT/PATCH/DELETE /api/products/{id}`。
- 匿名購物車：建立、讀取、刪除購物車，以及購物車商品新增累加、查詢、數量更新、移除與清空。
- 結帳與訂單：保留 `POST /api/orders`，另提供 `POST /api/carts/{cart_id}/checkout` 建立訂單並清空購物車。
- 健康檢查：`GET /api/health` 回傳 `{"status":"ok"}`。

## 資料與服務說明

Compose 只啟動 `frontend` 與 `backend` 兩個 containers；Nginx 是 `frontend` container 內的程序，不是第三個 Compose service。`backend` 將 `/data` 掛載至 `shop-data` named volume，資料庫檔案為 `/data/shop.db`；另將 `/logs` 掛載至獨立的 `shop-logs` named volume。商品只在資料庫第一次初始化時種子一次，既有訂單會保留商品快照。

直接開啟 <http://localhost:8000/docs> 可查看 FastAPI 自動產生的 Swagger UI；從商店頁面使用 API 時，前端則透過 <http://localhost:8080/api/> 代理到同一個後端。

## Backend 依賴與 logging

Backend 要求 Python 3.12。`backend/pyproject.toml` 宣告直接依賴，`backend/uv.lock` 固定完整依賴樹；Docker build 使用 uv 以 frozen lock 執行 `uv sync --frozen --no-dev --no-cache`。`backend/requirements.txt` 是由 lock 匯出的部署清單，更新依賴後可在 `backend/` 執行：

```sh
uv export --frozen --format requirements.txt --no-dev --no-hashes --output-file requirements.txt
```

Backend 的 `shop` logger 預設使用 `LOG_LEVEL=INFO` 與 `LOG_DIR=/logs`。每筆 application log 同時輸出到 stdout 與 UTF-8 編碼的 `/logs/app.log`，格式含 UTC ISO-8601 毫秒時間、level、logger name 與訊息；`app.log` 在每日 UTC 午夜輪替，檔名為 `app.log.YYYY-MM-DD`，保留最近 7 份，跨過午夜後由下一筆記錄觸發輪替。啟動、資料庫 migration、HTTP request（method、path、status、duration_ms）與未預期例外都會記錄；request log 不含 query，例外 traceback 不含 request body。

## 驗證狀態

已在預設 `nightwatch-shop-web` project、前端 8080 與 backend 8000 完成驗證：`make up COMPOSE=docker-compose` 成功建置兩個 images 並重建兩個 healthy containers；`make test COMPOSE=docker-compose` 的 10 tests 全部通過；`make smoke` 與 8080 proxy 的商品 POST/PATCH、購物車建立/加購/讀取現價重算/結帳清空/DELETE 通過；OpenAPI 含 9 個 paths 及 `ProductResponse`、`CartResponse`、`OrderResponse` schemas，超大 ID 的 GET、cart body/path 與 orders request 均回傳 422。

uv `lock --check` 通過，fresh export 與 `requirements.txt` 的依賴內容一致；runtime 使用 UID 10001 的 `.venv` Uvicorn，`/data` 與 `/logs` 可由 `app` 寫入。`app.log` 的 UTC ISO/INFO startup、migration、HTTP 200/201/204/404/422、無 query path，以及 INFO/ERROR traceback、每日輪替與 restart 後 `/logs` 保留均通過檢查。

尚未執行 browser 自動化與 HTTP 500 error path；破壞性 `make clean CONFIRM=yes` 尚未執行。`make help` 與未帶 `CONFIRM=yes` 的 clean guard 已驗證。前端購物袋仍使用 `localStorage`，未串接 cart API。

## 文件與交付紀錄

變更紀錄與討論紀錄現放在 `docs/`。歷史版本曾依需求使用 `shop-web-eample/docs/`，該舊路徑只保留在紀錄脈絡中：

- [CHANGELOG.md](docs/CHANGELOG.md)
- [DISCUSSION.md](docs/DISCUSSION.md)
- [API.md](docs/API.md)
- [Backend README](backend/README.md)
- [Backend CHANGELOG](backend/CHANGELOG.md)

驗證結果請見 [CHANGELOG](docs/CHANGELOG.md)。
