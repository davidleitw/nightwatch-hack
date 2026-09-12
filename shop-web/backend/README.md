# Backend

`shop-web/backend/` 提供日日選物的 FastAPI API、SQLite 資料保存與 application logging。完整 endpoint、request/response 欄位、限制與錯誤狀態碼請見 [../docs/API.md](../docs/API.md)。

## 商品 CRUD

`/api/products` 提供商品清單、單筆讀取、建立、完整更新、部分更新與刪除：

| 操作 | Endpoint | 成功回應 |
| --- | --- | --- |
| 列出商品 | `GET /api/products` | 200，商品陣列 |
| 讀取商品 | `GET /api/products/{id}` | 200，商品物件 |
| 建立商品 | `POST /api/products` | 201，商品物件 |
| 完整更新 | `PUT /api/products/{id}` | 200，商品物件 |
| 部分更新 | `PATCH /api/products/{id}` | 200，商品物件 |
| 刪除商品 | `DELETE /api/products/{id}` | 204，無 body |

商品欄位為 `name`、`category`、`price`、`icon`、`color`、`description`。POST 與 PUT 必須提供全部六欄位；PATCH 至少提供一欄。`name`、`category`、`icon` 會 trim 後檢查不可為空，長度上限分別為 80、40、32；`description` 最長 2,000 字元且可為空；`price` 必須是嚴格整數 1..1,000,000,000 NTD；`color` 必須符合 `#RRGGBB`。`id` 由 server 自動給定，path 中的商品 ID 必須是 1..9,223,372,036,854,775,807 的整數；寫入 request 的 `id`、未知欄位與 `null` 都回傳 422。

不存在的商品回傳 404。刪除商品會以資料庫的 foreign key cascade 同步移除購物車中的該商品；已建立訂單的商品快照不會改變。

## 匿名購物車 CRUD

`/api/carts` 不需要登入或 token，提供購物車與商品項目的完整生命週期：

| 操作 | Endpoint | 成功回應 |
| --- | --- | --- |
| 建立購物車 | `POST /api/carts` | 201，`{id, items, total}` |
| 讀取購物車 | `GET /api/carts/{cart_id}` | 200，購物車 |
| 刪除購物車 | `DELETE /api/carts/{cart_id}` | 204，無 body |
| 新增/累加商品 | `POST /api/carts/{cart_id}/items` | 200，購物車 |
| 讀取商品明細 | `GET /api/carts/{cart_id}/items` | 200，items 陣列 |
| 設定商品數量 | `PATCH /api/carts/{cart_id}/items/{product_id}` | 200，購物車 |
| 移除商品 | `DELETE /api/carts/{cart_id}/items/{product_id}` | 200，購物車 |
| 清空購物車 | `DELETE /api/carts/{cart_id}/items` | 200，空購物車 |

建立購物車不帶 request body。新增商品使用 `{product_id, quantity}`，既有商品會累加；設定數量使用 `{quantity}`，是絕對數量。`quantity` 必須是嚴格整數 1..99，累加後超過 99 回傳 400。購物車 response 的每個 line 包含完整商品欄位、`product_id`、`quantity` 與 server 計算的 `line_total`，`total` 是所有 line 的總和；商品價格變更後，讀取或修改購物車會用目前價格重算。

未知購物車、商品或購物車商品項目回傳 404；request schema 或 path 不符合限制回傳 422。移除商品使用 DELETE，不以 quantity 0 代替。

## 結帳與訂單資料庫

保留 `POST /api/orders`，request 為 `{name, address, items}`：`name` trim 後長度 1..80，`address` trim 後長度 5..300，`items` 為 1..50 個 `{product_id, quantity}`，其中 ID 為正整數、數量為嚴格整數 1..99。未知商品回傳 400；相同商品會合併數量，合併後超過 99 件也回傳 400。成功回傳 201 與訂單 response。

另提供 `POST /api/carts/{cart_id}/checkout`，request 只有 `{name, address}`。後端以目前商品價格建立訂單，成功回傳 201，並在同一個 SQLite transaction 中清空購物車；未知購物車回傳 404，空購物車回傳 400，欄位不符回傳 422。訂單 response 包含 `id`、`created_at`、`total` 與商品快照 `items`；配送姓名與地址會保存於資料庫 payload。

## Demo 故障卡與結帳故障注入

`/api/demo-faults` 是單一 Uvicorn process 內的記憶體控制 API。GET、POST、DELETE 成功都回傳相同格式：

```json
{
  "cards": [
    {"fault_id": "checkout_exception", "title": "結帳例外", "description": "..."},
    {"fault_id": "database_write_lock", "title": "資料庫寫入鎖", "description": "..."},
    {"fault_id": "checkout_delay", "title": "結帳延遲", "description": "..."}
  ],
  "active": {
    "fault_id": "checkout_delay",
    "started_at": "2026-09-12T00:00:00.000Z",
    "expires_at": "2026-09-12T00:01:00.000Z",
    "remaining_seconds": 59.9,
    "status": "active"
  },
  "lease_seconds": 60,
  "delay_seconds": 10
}
```

POST body 為 `{ "fault_id": "..." }`，故障卡一次只能啟用一張；未知 ID 或多餘欄位回傳 422，已有 active、activating 或 restoring 狀態回傳 409。DELETE 不帶 body 且可重複呼叫，回傳 `active: null`。lease 固定 60 秒，以 monotonic clock 判斷期限，`expires_at` 與 `started_at` 僅供 UTC UI 顯示；期限到期前後都會先完成資源清理才允許下一次啟用。

三張卡的行為如下：`checkout_exception` 在兩個 checkout transaction 的實際 order/cart 寫入後、commit 前 raise `RuntimeError`，因此整筆 transaction rollback；`database_write_lock` 由專用 thread 建立並持有自己的 SQLite connection 與 `BEGIN IMMEDIATE`，成功取得後才回報 active，取得失敗會釋放並回傳錯誤；`checkout_delay` 在兩個 checkout 入口的 transaction 前以非阻塞方式等待 10 秒，DELETE 或自動清理會喚醒等待中的 request。故障 request 的 log 只記 fault ID、method、path、status 與 duration，不記 request body。

SQLite 預設使用 `/data/shop.db`，由 Compose 的 `shop-data` named volume 保存。商品只在資料庫第一次初始化時種子一次；重啟不會重新加入已刪除商品，既有 orders 會保留自己的商品快照。

## 架構與執行

- FastAPI 負責商品、購物車與訂單路由及 schema 驗證。
- `/data/shop.db` 由 `shop-data` named volume 保存。
- `/logs/app.log` 由獨立的 `shop-logs` named volume 保存。
- 直接 API 與 Swagger UI 使用 `http://localhost:8000`；商店頁面經 Nginx 的 API proxy 使用 `http://localhost:8080/api`。
- 本專案是本機購物 demo，商品寫入 API 無 auth，沒有管理介面、登入權限或金流。

## 依賴與容器環境

Backend 使用 Python 3.12。`pyproject.toml` 宣告 FastAPI 與 Uvicorn，`uv.lock` 固定完整依賴樹；Docker image 內使用 uv 0.12.13，執行 `uv sync --frozen --no-dev --no-cache` 建立環境，應用程式以非 root 的 `app` 使用者執行。

本機要依照 lock 安裝執行所需依賴（不含 dev dependencies）時，在 `backend/` 執行：

```sh
uv sync --frozen --no-dev
```

`requirements.txt` 由 lock 匯出，檔案開頭記錄產生方式。更新 `pyproject.toml` 並重新鎖定後，在 `backend/` 執行：

```sh
uv export --frozen --format requirements.txt --no-dev --no-hashes --output-file requirements.txt
```

## Application logging

`shop` logger 預設使用 `LOG_LEVEL=INFO`，logging 目錄由 `LOG_DIR` 設定，預設為 `/logs`。每筆 application log 同時輸出到 stdout 與 UTF-8 編碼的 `app.log`；格式為 UTC ISO-8601 毫秒 timestamp、level、logger name 與訊息。`app.log` 以 UTC 每日午夜輪替，輪替檔名為 `app.log.YYYY-MM-DD`，保留 7 份備份；跨過午夜後由下一筆記錄觸發輪替。

服務啟動、資料庫 migration、HTTP request（method、path、status、duration_ms）與未預期例外都會記錄；request path 不含 query，例外 traceback 不記錄 request body（包含姓名、地址與 token）。`LOG_LEVEL` 或 `LOG_DIR` 設定無效時，logging 初始化會輸出錯誤並讓啟動失敗；Uvicorn/root logger 不由 application logger 改寫。

從 `shop-web/` 執行：

```sh
make up
make test
```

目前已完成預設 8080/8000 環境驗證：兩個 containers 建置與重建成功，10 項 backend 整合測試全部通過，`make smoke` 及商品、購物車、結帳 API 流程通過，containers 均為 healthy，OpenAPI 含 10 個 paths 與 `ProductResponse`、`CartResponse`、`OrderResponse` schemas。另以 isolated backend image 實測三張 demo fault、兩種 rollback、10 秒 delay/DELETE 喚醒、SQLite lock 約 10.18 秒 timeout 與清理；backend restart 後 fault inactive 且寫入恢復，log 含 traceback、500 status 與 fault ID。Chrome CDP 已實測事件頁真實 DOM 啟用/解除並取得 desktop/mobile 截圖；visual review 確認 desktop 三卡完整、390px mobile 單欄無溢出，`main.py`/`demo_faults.py` workspace 與 container hash 一致，前端部署 asset 為本次 production build。`checkout_delay` 的 TTL 自動喚醒已驗證，`database_write_lock` 的 60 秒自動到期尚未單獨驗證。前端仍以 `localStorage` 保存購物袋，未串接 cart CRUD 或 cart checkout API。
