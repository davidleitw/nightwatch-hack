# 商品、購物車與訂單 API

本文件是 `shop-web` 的現行 API 契約摘要。瀏覽器與外部呼叫者使用 gateway `backend:8000`；gateway 將商品、匿名購物車、checkout、訂單與故障控制轉送到 `catalog`、`cart`、`order` 三個內部服務。公開路由與 request/response body 保持原形狀。

## Base URL

```sh
BASE_URL=http://localhost:8000
# 或經 frontend Nginx proxy
BASE_URL=http://localhost:8080
```

公開 port 是 gateway `8000` 與 frontend `8080`；catalog/cart/order 僅使用 Compose network 的 `8000`，不發布 host port。所有帶 body 的 request 使用 `Content-Type: application/json`。成功 response 依 endpoint 使用 200、201 或 204；204 沒有 body。

## 共通資料規則與錯誤

- request schema 拒絕 `null`、未列欄位與錯誤型別；驗證失敗回傳 422。
- 商品與 cart item 的 `product_id` 範圍為 1..9,223,372,036,854,775,807；超出範圍或非正整數回傳 422。
- `price` 是嚴格正整數且不得超過 1,000,000,000；`quantity` 是嚴格整數且範圍 1..99。
- `cart_id` 必須是至少 1 個字元的字串；空值回傳 422。
- gateway 無法連到下游時回傳 503，並在 gateway log 記錄 dependency；下游自己的 4xx/5xx body 與 status 會透傳。
- 找不到購物車、商品或指定購物車商品項目回傳 404；空 cart checkout 與累加超過 99 回傳 400。

## 商品 API

商品 endpoint 由 `catalog` 擁有，gateway 保留以下公開路由：

| Method | Path | 成功 response | 失敗狀態 |
| --- | --- | --- | --- |
| GET | `/api/products` | 200，商品陣列，依 `id` 排序 | 下游不可用 503 |
| GET | `/api/products/{id}` | 200，商品物件 | 商品不存在 404；path 不符 422 |
| POST | `/api/products` | 201，商品物件 | request 不符 422；下游不可用 503 |
| PUT | `/api/products/{id}` | 200，商品物件 | 商品不存在 404；request/path 不符 422 |
| PATCH | `/api/products/{id}` | 200，商品物件 | 商品不存在 404；request/path 不符 422 |
| DELETE | `/api/products/{id}` | 204，無 body | 商品不存在 404；path 不符 422 |

商品欄位為 `id`、`name`、`category`、`price`、`icon`、`color`、`description`。文字欄位會 trim；POST/PUT 需完整提供六個可寫欄位，PATCH 至少一欄。商品刪除會由 cart 在後續讀取時 lazy prune 該商品；既有訂單 snapshot 不受影響。

## 匿名購物車 API

購物車不需要登入或 token。建立購物車：

```sh
curl -X POST "$BASE_URL/api/carts"
```

回傳：

```json
{"id":"uuid4-hex","items":[],"total":0}
```

每個 item 包含完整商品欄位、`product_id`、`quantity`、`line_total`；`total` 是所有 line 的 server 計算總和。商品價格變更後，cart 讀取或變更時以 catalog 現價重算；catalog 不可用時不會把未知商品誤判為已刪除。

| Method | Path | 成功 response | 失敗狀態 |
| --- | --- | --- | --- |
| POST | `/api/carts` | 201，`{id, items, total}` | 下游不可用 503 |
| GET | `/api/carts/{cart_id}` | 200，購物車 | cart 不存在 404；catalog/cart 不可用 503 |
| DELETE | `/api/carts/{cart_id}` | 204，無 body | cart 不存在 404 |
| POST | `/api/carts/{cart_id}/items` | 200，購物車 | cart/product 不存在 404；超過 99 為 400 |
| GET | `/api/carts/{cart_id}/items` | 200，items 陣列 | cart 不存在 404 |
| PATCH | `/api/carts/{cart_id}/items/{product_id}` | 200，購物車 | cart/item 不存在 404；request/path 不符 422 |
| DELETE | `/api/carts/{cart_id}/items/{product_id}` | 200，購物車 | cart/item 不存在 404 |
| DELETE | `/api/carts/{cart_id}/items` | 200，空購物車 | cart 不存在 404 |

新增 item body 是 `{ "product_id": 1, "quantity": 2 }`，既有 item 會累加；PATCH body 是 `{ "quantity": 3 }` 的絕對數量。移除使用 DELETE，不以 quantity 0 代替。

## 訂單與 checkout

### `POST /api/orders`

既有路由仍由 gateway 轉送至 order。body 包含 `name`、`address`、`items`，成功回傳 201 order response，且訂單保存商品 name/price/quantity snapshot。可選擇帶 `Idempotency-Key` header；同一 key 搭配不同內容回傳 409，重試同一內容會回傳同一操作結果。

```sh
curl -X POST "$BASE_URL/api/orders" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-order-key' \
  -d '{"name":"測試顧客","address":"台北市測試路 1 號","items":[{"product_id":1,"quantity":2}]}'
```

### `POST /api/carts/{cart_id}/checkout`

前端使用此路由；body 只有 `name` 與 `address`，商品明細與總額由 cart/order server 端取得。可選擇帶 `Idempotency-Key` header；前端每一筆新的 cart/shipping 內容生成 key，HTTP 失敗保留 key 供同內容重試；網路中斷或 HTTP 5xx 時前端鎖定修改，使用保存的原始 body 與同一 key 重試。未知 cart 回傳 404、空 cart 回傳 400、schema/path 不符回傳 422；下游 service 不可用回傳 503。

```sh
curl -X POST "$BASE_URL/api/carts/$CART_ID/checkout" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-cart-checkout-key' \
  -d '{"name":"測試顧客","address":"台北市測試路 1 號"}'
```

order coordinator 會向 cart prepare 保留購物袋 snapshot，向 catalog lookup 當下商品資料，成功寫入 order 後 complete cart；失敗會 abort/補償。這是跨 SQLite service 的恢復流程，不宣稱是單一 SQLite transaction。服務重啟時 order 會 reconcile 尚未完成的操作。cart 的 abort 在 prepare 尚未建立時也會保存 `aborted` 紀錄並回 204；遲到的同 operation prepare 回傳空 `items`，資料庫仍維持 `aborted`，不會保留購物車。

## Demo 故障卡 API

故障狀態由 `order` 擁有，gateway 轉送以下路由：

| Method | Endpoint | Request | 成功回應 | 錯誤 |
| --- | --- | --- | --- | --- |
| GET | `/api/demo-faults` | — | 200，故障卡 response | order 不可用 503 |
| POST | `/api/demo-faults` | `{ "fault_id": "checkout_exception" }` | 200，故障卡 response | 未知 ID 422；已有 active/transition 409；lock 取得失敗 503 |
| DELETE | `/api/demo-faults` | 無 body | 200，`active: null` | order 不可用 503 |

固定 ID 是 `checkout_exception`、`database_write_lock`、`checkout_delay`。三張卡只作用 order：exception 在訂單 transaction commit 前 raise 並 rollback；database lock 只鎖 order SQLite 寫入，其他 catalog/cart API 不受該鎖影響；delay 在 order checkout 前等待 10 秒。故障會持續啟用，沒有自動 lease 或 expiry；response 的 `lease_seconds`、active 的 `expires_at` 與 `remaining_seconds` 為 `null`，active 仍提供 `fault_id`、`started_at`、`status`。只有 DELETE 可解除，process shutdown 會清理 lock worker 並喚醒 delay，頁面約每 2 秒依 server 狀態輪詢。

## 資料、服務與 logging

- 舊 monolith volume `shop-data:/legacy:ro` 只在 catalog/cart/order startup 讀取，各服務匯入自己的表；`shop-data` 不由 `make up` 刪除。
- 後續資料分別在 `catalog-data:/data`、`cart-data:/data`、`order-data:/data`；gateway 沒有 data volume。
- logs 分別在 `catalog-logs`、`cart-logs`、`order-logs` 與 gateway `shop-logs` 的 `/logs/app.log`。
- 預設 `LOG_LEVEL=INFO`、`LOG_DIR=/logs`；log 使用 UTC ISO-8601 毫秒、level、logger name 與訊息，HTTP path 不含 query/request body，app.log 每日 UTC 午夜輪替並保留 7 份。
- 具體查看方式：`docker-compose -p nightwatch-shop-web -f compose.yaml logs --tail=100 backend catalog cart order frontend`。

## 驗證紀錄

本輪已完成五服務 production build 與重建，`frontend`、`backend`、`catalog`、`cart`、`order` 全部 healthy。`make test COMPOSE=docker-compose` 的既有 10 項測試全數通過（23.926 秒），`make smoke COMPOSE=docker-compose` 的首頁與 Nginx `/api/health` 通過。

真實 HTTP 驗證確認三張故障卡各自超過 65 秒仍 active，DELETE 後恢復；資料庫鎖約 10.10 秒後回 500，失敗未新增訂單。catalog/cart/order 停止與恢復、503 錯誤、商品與購物車保留，以及同 `Idempotency-Key` 重試已實測。以真實 cart SQLite 寫入鎖造成「訂單已提交但清空購物車失敗」後，同 key 重試與 order 重啟恢復均只保留一張訂單並清空購物車。abort 早於 prepare 與重複 abort 也已驗證。

Chrome 實際操作已確認六張商品圖載入、舊 `daily-cart` 只匯入一次、重新整理保留 server cart、瀏覽器斷線與 HTTP 500 後以原 key/body 重試成功、事件卡啟用與手動解除，以及 1440px/390px 事件頁沒有水平溢出。所有請求使用真實服務，未以 fake fetch 或 stub 代替。

實際比對 legacy volume 的 6 件商品與 34 張訂單，ID、內容與時間均保留（訂單 payload 在新庫由 TEXT 轉成 BLOB，解碼後內容一致）；舊 carts/cart_items 各為 0，因此沒有真實非空 legacy cart 可比對。四個後端服務的 `/logs/app.log` 都有 HTTP 紀錄。驗證商品與購物車已清理，驗證訂單保留，因 API 沒有刪除訂單功能。

未執行破壞性的 `make clean CONFIRM=yes`、跨主機部署、同服務多副本或實際金流／出貨；本輪也未等待跨 UTC 午夜驗證 log 輪替。故障狀態仍在 order 記憶體內，order process 重啟會清除；取消的是原本的 60 秒自動到期。
