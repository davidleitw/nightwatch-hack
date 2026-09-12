# 日日選物（shop-web）

日日選物是本機購物 demo：前端使用 React + Vite，公開 API 由 FastAPI gateway 提供，商品、匿名購物袋與訂單分成三個內部服務。Docker Compose 會啟動五個 containers：`frontend`、`backend`（gateway）、`catalog`、`cart`、`order`。前端容器內的 Nginx 將 `/api/` 代理到 `backend:8000`。

## 服務與資料

| container | 程式 | 對外 | 私有資料與 log |
| --- | --- | --- | --- |
| `frontend` | Nginx 提供 production React asset | `127.0.0.1:8080` | — |
| `backend` | `app.main:app` gateway | `127.0.0.1:8000` | `shop-logs:/logs` |
| `catalog` | `app.catalog:app` 商品服務 | 僅 Compose network `8000` | `catalog-data:/data`、`catalog-logs:/logs` |
| `cart` | `app.cart:app` 匿名購物袋服務 | 僅 Compose network `8000` | `cart-data:/data`、`cart-logs:/logs` |
| `order` | `app.order:app` 訂單與 checkout coordinator | 僅 Compose network `8000` | `order-data:/data`、`order-logs:/logs` |

三個業務服務在首次啟動時以 `shop-data:/legacy:ro` 讀取舊 monolith 的 `/legacy/shop.db`，各自只匯入自己擁有的資料表；schema migration version 會防止重啟重複匯入。`shop-data` 只讀掛載且不會被 `make up` 刪除，新的 `catalog-data`、`cart-data`、`order-data` 分別保存後續資料。`make down`、`make stop` 保留 volumes；`make clean CONFIRM=yes` 會刪除 legacy、三個業務資料庫、四組 application logs 與 containers/network。

服務間使用 Compose DNS：`CATALOG_URL=http://catalog:8000`、`CART_URL=http://cart:8000`、`ORDER_URL=http://order:8000`。瀏覽器只接觸 gateway，仍使用原本的商品、購物車、`POST /api/orders` 與故障演練 URL。

## 快速啟動

需求：Docker（含 Compose）、GNU Make，以及 smoke check 所需的 `curl`。

```sh
cd shop-web
make up
```

`make up` 會先停止同一 Compose project 中的舊 `frontend` 與 `backend` entrypoint，再 build/up 五個服務；這一步不刪資料。預設網址：

- 商店：<http://localhost:8080/>
- 故障演練：<http://localhost:8080/#/events>
- gateway Swagger UI：<http://localhost:8000/docs>
- 經 Nginx 代理的 health：<http://localhost:8080/api/health>

預設 project name 是 `nightwatch-shop-web`。若直接使用 `docker-compose`，也要帶相同 project name，例如：

```sh
docker-compose -p nightwatch-shop-web -f compose.yaml ps
docker-compose -p nightwatch-shop-web -f compose.yaml logs --tail=100 backend catalog cart order
```

## Makefile 指令

```sh
make help
make build
make up                         # 先停 frontend/backend，再建置並啟動五服務
make down                       # 移除 containers/network，保留所有 volumes
make stop                       # 停止 containers，保留 containers/volumes
make restart
make logs
make ps
make config
make test
make smoke
make clean CONFIRM=yes         # 會刪除所有資料與 logs volumes
```

`COMPOSE`、`PROJECT_NAME`、`FRONTEND_PORT`、`BACKEND_PORT` 可覆寫；例如 `make up COMPOSE=docker-compose`。`make test` 在 gateway `backend` container 內執行既有 10 cases，測試 harness 會再啟動暫時的四個 uvicorn app。`make smoke` 檢查前端首頁與 Nginx 代理的 `/api/health`。

`restart.sh` 可從任意工作目錄呼叫：

```sh
./restart.sh          # build/recreate/wait/health check
./restart.sh --open   # 啟動現有 containers，不重新 build
./restart.sh --close  # 停止服務，保留 containers 與 volumes
```

## 使用流程

首頁透過 `GET /api/products` 載入商品，依商品 API 的 `name` 匹配六張 seed 商品圖；自訂商品沒有映射時保留 API 的 `icon` fallback。映射與素材位於 [frontend/public/images/products/README.md](frontend/public/images/products/README.md)。

前端購物袋以 `localStorage` 的 `daily-cart-id` 保存 server cart ID。若瀏覽器仍有舊版 `daily-cart` 內容，頁面會先取得商品清單，建立 cart，逐項以 server API 匯入；所有匯入成功後才移除舊 key。舊內容中的商品若已不存在，頁面會明確顯示商品 ID 並保留舊內容。商品數量由 `POST/PATCH/DELETE /api/carts/{cart_id}/items` 更新，畫面使用 server 的 `line_total` 與 `total`。

結帳使用 `POST /api/carts/{cart_id}/checkout`，每次新的購物袋/配送內容會生成 `Idempotency-Key`。HTTP 失敗會保留同一 key 供重試；網路中斷或 HTTP 5xx 時會保留原始收件資料並暫停購物袋與配送欄位修改，要求先用同一 key 與內容重試，以免重複訂單。既有 `POST /api/orders` 仍保留給 API 使用者，完整欄位見 [docs/API.md](docs/API.md)。本 demo 不串接金流、扣款或出貨，請勿填寫真實個人資料。

跨服務 checkout 由 `order` 協調 `cart` 的 prepare/complete/abort 與 `catalog` 的現價 lookup；cart 會保存尚未收到 prepare 的取消紀錄，避免遲到的 prepare 重新鎖住購物袋；服務中斷時 gateway 回傳 503，恢復後可重試同一 idempotency key。商品刪除後 cart 會在下一次讀取時 lazy prune 缺少的商品；已建立訂單保存自己的 snapshot。

## 故障演練

從商店頁的「故障演練」入口，或直接開啟 <http://localhost:8080/#/events>。頁面固定提供 `checkout_exception`、`database_write_lock`、`checkout_delay` 三張卡；三張卡都作用於 `order` service，商品與購物袋服務維持可觀察的獨立狀態。

- `checkout_exception`：訂單提交前 raise，訂單資料回滾，回傳 500。
- `database_write_lock`：只鎖訂單資料庫寫入；其他訂單寫入最多等 10 秒後回傳 500，catalog/cart 維持可用。
- `checkout_delay`：order checkout 等待 10 秒；不會自動建立訂單。

事件頁使用 `GET/POST/DELETE /api/demo-faults`，一次只允許一張卡；故障會持續啟用，只有 DELETE 手動解除。頁面約每 2 秒輪詢 server 狀態，操作期間防止重送；離開頁面會停止輪詢。控制 API 經 gateway 轉送到 order，order 停止時會如實顯示 API 錯誤。

## API、logging 與 uv

- 商品、cart、checkout、訂單與 fault endpoint：見 [docs/API.md](docs/API.md)。
- Compose API port：gateway `8000`；catalog/cart/order 只在內部使用 `8000`。
- 各服務的 logger 名稱為 `shop`，預設 `LOG_LEVEL=INFO`、`LOG_DIR=/logs`；每個服務把 UTF-8 log 寫到自己的 `/logs/app.log` 並輸出 stdout。
- log 格式包含 UTC ISO-8601 毫秒時間、level、logger name、訊息；HTTP log 只記 method/path/status/duration，不記 query 或 request body。`app.log` 於 UTC 每日午夜輪替並保留 7 份。
- 查閱所有服務：`docker-compose -p nightwatch-shop-web -f compose.yaml logs --tail=100 backend catalog cart order frontend`。
- 查閱 named volumes：`docker volume ls --filter name=nightwatch-shop-web`；不要在 migration 前刪除 `nightwatch-shop-web_shop-data`。

Backend 使用 Python 3.12。`backend/pyproject.toml` 宣告直接依賴，`backend/uv.lock` 固定完整依賴，Docker 使用 `uv sync --frozen --no-dev --no-cache`；`backend/requirements.txt` 保留作 pip fallback，更新依賴後在 `backend/` 執行：

```sh
uv export --frozen --format requirements.txt --no-dev --no-hashes --output-file requirements.txt
```

本機開發需讓 `DB_PATH`、`ORDER_DB_PATH`、`LOG_DIR` 指向可寫目錄；容器預設使用 `/data`、`/logs`。不應讓本機不可寫的 `/logs` 成為直接啟動的設定。

## 驗證狀態

本輪已完成五服務 production build 與重建，`frontend`、`backend`、`catalog`、`cart`、`order` 全部 healthy。`make test COMPOSE=docker-compose` 的既有 10 項測試全數通過（23.926 秒），`make smoke COMPOSE=docker-compose` 的首頁與 Nginx `/api/health` 通過。

真實 HTTP 驗證確認三張故障卡各自超過 65 秒仍 active，DELETE 後恢復；資料庫鎖約 10.10 秒後回 500，失敗未新增訂單。catalog/cart/order 停止與恢復、503 錯誤、商品與購物車保留，以及同 `Idempotency-Key` 重試已實測。以真實 cart SQLite 寫入鎖造成「訂單已提交但清空購物車失敗」後，同 key 重試與 order 重啟恢復均只保留一張訂單並清空購物車。abort 早於 prepare 與重複 abort 也已驗證。

Chrome 實際操作已確認六張商品圖載入、舊 `daily-cart` 只匯入一次、重新整理保留 server cart、瀏覽器斷線與 HTTP 500 後以原 key/body 重試成功、事件卡啟用與手動解除，以及 1440px/390px 事件頁沒有水平溢出。所有請求使用真實服務，未以 fake fetch 或 stub 代替。

實際比對 legacy volume 的 6 件商品與 34 張訂單，ID、內容與時間均保留（訂單 payload 在新庫由 TEXT 轉成 BLOB，解碼後內容一致）；舊 carts/cart_items 各為 0，因此沒有真實非空 legacy cart 可比對。四個後端服務的 `/logs/app.log` 都有 HTTP 紀錄。驗證商品與購物車已清理，驗證訂單保留，因 API 沒有刪除訂單功能。

未執行破壞性的 `make clean CONFIRM=yes`、跨主機部署、同服務多副本或實際金流／出貨；本輪也未等待跨 UTC 午夜驗證 log 輪替。故障狀態仍在 order 記憶體內，order process 重啟會清除；取消的是原本的 60 秒自動到期。
