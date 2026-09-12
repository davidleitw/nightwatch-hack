# Backend 變更紀錄

## [Unreleased] — 2026-09-12

後端功能模組已完成，以下內容以 `backend/app/main.py` 與相關 runtime 設定為準。預設 8080/8000 環境的建置、10 項整合測試、smoke check、超大 ID 邊界、uv 環境與 logging 行為均已驗證。

### 商品 CRUD

- 提供 `GET/POST /api/products` 與 `GET/PUT/PATCH/DELETE /api/products/{id}`。
- POST/PUT 要求 `name`、`category`、`price`、`icon`、`color`、`description` 六欄位；PATCH 至少一欄，拒絕 `id`、未知欄位與 `null`。
- `name`、`category`、`icon` trim 後不可為空，長度上限分別為 80、40、32；`description` 可為空且最多 2,000 字元；`color` 必須是 `#RRGGBB`。
- `price` 為嚴格整數 1..1,000,000,000 NTD；path 中的商品 ID 限制為 1..9,223,372,036,854,775,807，無效值回傳 422。
- 刪除商品會同步移除購物車中的該商品，既有訂單快照維持不變。

### 匿名購物車 CRUD

- 提供無 body 建立購物車、讀取/刪除購物車，以及 items 的新增累加、查詢、絕對數量更新、單項移除與全部清空。
- 每個 line 回傳完整商品欄位、`product_id`、`quantity`、`line_total`；`total` 由 server 依目前價格計算。
- `quantity` 限制為嚴格整數 1..99；累加超過 99 回傳 400，未知 cart/product/item 回傳 404，schema 或 path 不符回傳 422。

### 結帳與訂單資料庫

- 保留 `POST /api/orders` 的 `{name,address,items}` request；`name` 為 1..80 字元、`address` 為 5..300 字元、`items` 為 1..50 筆商品與數量。
- 新增 `POST /api/carts/{cart_id}/checkout`，以 `{name,address}` 建立訂單並在同一個 transaction 中清空購物車；空 cart 回傳 400。
- SQLite 使用 `/data/shop.db`；商品只在資料庫第一次初始化時種子一次，重啟不還原已刪除商品，既有 orders 保留商品快照。

### Demo 故障卡

- 新增 `/api/demo-faults` 的 GET/POST/DELETE 控制 API，提供 `checkout_exception`、`database_write_lock`、`checkout_delay` 三張卡；回應包含卡片清單、active 狀態、60 秒 lease 與 10 秒延遲設定。
- 故障狀態只存在單一 process 記憶體，使用 monotonic TTL 並在 lifespan shutdown 清理；控制 API 為 async，不占用 SQLite route 的 sync threadpool。
- `checkout_exception` 在真實 order/cart 寫入後於 commit 前 raise 以驗證 rollback；`database_write_lock` 以專用 thread/connection 持有 `BEGIN IMMEDIATE`；`checkout_delay` 以可喚醒的非阻塞等待影響兩個 checkout 入口。

### uv 依賴與執行環境

- `pyproject.toml` 宣告 Python `>=3.12,<3.13` 與 FastAPI/Uvicorn 直接依賴，`uv.lock` 固定完整依賴樹。
- Docker image 使用 uv 0.12.13，執行 `uv sync --frozen --no-dev --no-cache`；`requirements.txt` 由 `uv export --frozen --format requirements.txt --no-dev --no-hashes --output-file requirements.txt` 產生。
- Container 建立非 root 的 `app` 使用者，並以 `/data`、`/logs` 目錄提供資料與 log 寫入權限。

### Application logging

- `shop` logger 預設 `LOG_LEVEL=INFO`、`LOG_DIR=/logs`，每筆 log 同時輸出至 stdout 與 UTF-8 編碼的 `/logs/app.log`。
- 格式含 UTC ISO-8601 毫秒 timestamp、level、logger name 與訊息；`app.log` 每日 UTC 午夜輪替為 `app.log.YYYY-MM-DD`，保留 7 份，跨過午夜後由下一筆記錄觸發輪替。
- 記錄 application startup/shutdown、database migration、HTTP request 的 method/path/status/duration_ms，以及未預期 request exception；request path 不含 query，exception traceback 不記錄 request body。
- Compose 以獨立 `shop-logs:/logs` named volume 保存 log；無效 logging 設定會讓啟動失敗並輸出錯誤。

### 文件與前端整合狀態

- [API.md](../docs/API.md) 完整列出 endpoint、request/response 欄位、限制、錯誤狀態碼與 curl 範例。
- 前端購物袋目前仍使用 `localStorage`，尚未串接 cart CRUD 或 cart checkout API。

### 驗證紀錄

- `make up COMPOSE=docker-compose` 成功建置並重建兩個 containers，兩者均 healthy；`make test COMPOSE=docker-compose` 的 10 tests 全部通過。
- `make smoke`、8080 proxy 的商品/購物車/結帳流程與 OpenAPI 10 paths、三個 response schemas 通過；超大 ID 的 GET、cart body/path 與 orders request 均回傳 422。
- `uv lock --check` 通過；fresh `uv export --frozen --no-dev --no-hashes` 與 `requirements.txt` 依賴內容一致。runtime 使用 UID 10001 的 `.venv` Uvicorn，`/data`、`/logs` 可由 `app` 寫入。
- logging 的 UTC ISO/INFO startup、migration、HTTP status/path/duration、INFO/ERROR traceback、每日 rollover 與 restart 後 log volume 保留通過檢查。
- isolated backend image 實測 demo fault controls 的 200/422/409、order/cart rollback、checkout delay 10 秒與 DELETE 喚醒、SQLite lock 約 10.18 秒後 500、手動釋放與 log traceback；backend restart 後 fault inactive 且寫入恢復。`checkout_delay` 剩餘約 8.64 秒時的 TTL 自動喚醒已驗證，`database_write_lock` 的 60 秒自動到期尚未單獨驗證。破壞性 `make clean CONFIRM=yes` 尚未執行。`make help` 與未帶 `CONFIRM=yes` 的 clean guard 已驗證。
- Chrome CDP 已以真實 DOM 驗證事件頁啟用/解除並取得 desktop/mobile 截圖；最終截圖 hash/visual review 尚待核對。
