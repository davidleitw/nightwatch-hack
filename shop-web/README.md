# 日日選物（shop-web）

日日選物是簡易購物示範網站：使用 React 19 與 Vite 建立前端，FastAPI 建立後端 API，透過 Docker Compose 啟動兩個服務。前端容器中的 Nginx 提供靜態頁面並將 `/api/` 請求代理到後端；訂單則寫入 SQLite named volume。

本文件日期：2026-09-12。實作分支為 `feat/simple-shop`。

## 專案結構

| 路徑 | 用途 |
| --- | --- |
| `frontend/` | React + Vite 前端；Docker multi-stage build 後由 Nginx 提供頁面 |
| `backend/` | FastAPI 應用程式、SQLite 存取與 API 整合測試 |
| `compose.yaml` | 定義 `frontend`、`backend` 兩個 containers 與 `shop-data` named volume |
| `Makefile` | Docker Compose 建置、啟停、檢查與測試入口 |

## 快速啟動

需求：已安裝 Docker（含 Compose v2）、GNU Make，以及執行 smoke check 所需的 `curl`。

```sh
cd shop-web
make up
```

Makefile 預設使用 Compose project name `nightwatch-shop-web`。因此 Compose 的 `shop-data` volume 實際名稱是 `nightwatch-shop-web_shop-data`，可與主機上其他 project 的同名 volume 分開保存；預設指令不會操作既有的 `shop-web_shop-data` volume。

服務啟動後可使用：

- 商店前端：<http://localhost:8080/>
- FastAPI Swagger UI：<http://localhost:8000/docs>
- 經 Nginx 代理的健康檢查：<http://localhost:8080/api/health>

預設連接埠只綁定到本機（`127.0.0.1`）。若要改用其他連接埠，可在執行 Make 時覆寫環境變數：

```sh
make up FRONTEND_PORT=3000 BACKEND_PORT=8001 PROJECT_NAME=nightwatch-shop-web-review
```

此時前端位於 `http://localhost:3000/`，後端文件位於 `http://localhost:8001/docs`，資料會使用另一個 `nightwatch-shop-web-review_shop-data` volume。前端容器仍以容器內的 Nginx port 80 運作，後端容器仍以 port 8000 接收 Compose 內部請求。`PROJECT_NAME` 可覆寫；每個不同 project name 都會對應獨立的 Compose containers、網路與 `shop-data` volume。

## Makefile 指令

Makefile 預設使用 `docker compose`，並在每個 Compose 指令加入 `-p $(PROJECT_NAME)`；預設值是 `PROJECT_NAME=nightwatch-shop-web`，設定來源都是 `compose.yaml`。也可以用 `COMPOSE=docker-compose` 指定舊版獨立 Compose CLI。下表的 Docker 指令以預設 project name 展開；若覆寫 `PROJECT_NAME`，請將 `nightwatch-shop-web` 換成指定的值。

| 指令 | 用途 | 實際 Docker Compose 操作 |
| --- | --- | --- |
| `make help` | 顯示所有指令用途；直接執行 `make` 也會顯示 | — |
| `make build` | 建置前後端 Docker images | `docker compose -p nightwatch-shop-web -f compose.yaml build` |
| `make rebuild` | 不使用快取重新建置 images | `docker compose -p nightwatch-shop-web -f compose.yaml build --no-cache` |
| `make up` | 建置並在背景啟動兩個 containers | `docker compose -p nightwatch-shop-web -f compose.yaml up -d --build` |
| `make down` | 移除 containers 與網路，保留訂單 volume | `docker compose -p nightwatch-shop-web -f compose.yaml down` |
| `make stop` | 停止 containers，保留 containers 與資料 | `docker compose -p nightwatch-shop-web -f compose.yaml stop` |
| `make restart` | 重新啟動 containers | `docker compose -p nightwatch-shop-web -f compose.yaml restart` |
| `make logs` | 持續查看前後端 log；按 `Ctrl-C` 離開 | `docker compose -p nightwatch-shop-web -f compose.yaml logs -f --tail=100` |
| `make ps` | 查看 containers 狀態 | `docker compose -p nightwatch-shop-web -f compose.yaml ps` |
| `make config` | 驗證並顯示 Compose 設定 | `docker compose -p nightwatch-shop-web -f compose.yaml config` |
| `make test` | 在已啟動的 backend container 執行 API 整合測試 | `docker compose -p nightwatch-shop-web -f compose.yaml exec -T backend python -m unittest discover -s tests -v` |
| `make smoke` | 檢查前端首頁與經 Nginx 代理的 API | `curl` 呼叫 `http://localhost:$(FRONTEND_PORT)/` 與 `/api/health` |
| `make clean CONFIRM=yes` | 移除 containers、網路與所有訂單資料 | `docker compose -p nightwatch-shop-web -f compose.yaml down --volumes --remove-orphans` |

`make test` 必須先執行同一個 `PROJECT_NAME` 的 `make up`，並會建立測試訂單。`make clean CONFIRM=yes` 只會刪除所選 `PROJECT_NAME` 的 `shop-data` volume 中的訂單資料；預設會刪除 `nightwatch-shop-web_shop-data`，不會刪除 `shop-web_shop-data`。少了 `CONFIRM=yes` 時，Makefile 會拒絕執行清理。

在舊版 experimental Compose 環境中，`make test` 可能只回傳 exit 0 而不顯示測試明細；若該環境的 backend container 名稱是 `nightwatch-shop-web_backend_1`，可直接查看測試輸出：

```sh
docker exec nightwatch-shop-web_backend_1 python -m unittest discover -s tests -v
```

建置或程式碼變更後，使用 `make up` 重新 build 並啟動；`make restart` 只重啟現有 containers，不會重新建置 image。

## 使用流程

首頁會從 `GET /api/products` 載入六項商品，可依分類篩選或以關鍵字搜尋。加入購物袋後可調整數量（每項最多 99 件），購物袋內容會暫存在瀏覽器的 `localStorage`。

結帳表單送出 `POST /api/orders`，後端會依伺服器端商品價格重新計算總額，並將訂單寫入 SQLite。這是模擬結帳，不會串接金流、扣款或出貨；頁面也提醒不要填寫真實個人資料。

## API 摘要

### `GET /api/health`

回傳 `{"status":"ok"}`，也用於 Compose healthcheck。

### `GET /api/products`

回傳商品目錄，包含 `id`、`name`、`category`、`price`、`icon`、`color` 與 `description`。

### `POST /api/orders`

請求內容：

```json
{
  "name": "測試顧客",
  "address": "台北市測試路 1 號",
  "items": [
    {"product_id": 1, "quantity": 2}
  ]
}
```

成功時回傳 HTTP 201 與訂單編號、建立時間、伺服器計算的總額及訂單明細。商品不存在、數量超過限制或欄位驗證失敗時，API 會回傳相應的 400 或 422 錯誤。

## 資料與服務說明

Compose 只啟動兩個 containers：`frontend` 與 `backend`。Nginx 是 `frontend` container 內的程序，不是第三個 Compose service。`backend` 將 `/data` 掛載至 `shop-data` named volume，預設實際名稱為 `nightwatch-shop-web_shop-data`，資料庫檔案為 `/data/shop.db`；執行同一個 `PROJECT_NAME` 的 `make down` 或 `make stop` 會保留這個 volume。更換 `PROJECT_NAME` 後會使用另一個 project-scoped volume。

直接開啟後端的 `/docs` 可查看 FastAPI 自動產生的 Swagger UI；從瀏覽器使用商店時，前端則透過 Nginx 的 `/api/` 代理存取同一個後端。

## 文件與交付紀錄

變更紀錄與討論紀錄現放在 `docs/`。歷史版本曾依需求使用 `shop-web-eample/docs/`，該舊路徑只保留在紀錄脈絡中：

- [CHANGELOG.md](docs/CHANGELOG.md)
- [DISCUSSION.md](docs/DISCUSSION.md)

驗證結果請見 [CHANGELOG](docs/CHANGELOG.md)。
