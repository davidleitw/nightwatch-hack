# 商品、購物車與訂單 API

本文件是 `shop-web` 的 API 契約，欄位與行為以 `backend/app/main.py` 為準，包含商品 CRUD、匿名購物車、既有訂單與購物車結帳。

本機測試與 demo 固定使用 frontend `8080` 與 backend `8000`。API 整合測試、smoke check、主要商品/購物車/結帳流程與三張 demo fault 的 backend HTTP 行為已完成；Chrome CDP 也已驗證事件頁真實 DOM 啟用/解除，最終截圖 hash/visual review 尚待核對。前端購物袋目前仍使用 `localStorage`，尚未串接新的購物車 API。

## Base URL

直接存取 FastAPI backend：

```sh
BASE_URL=http://localhost:8000
```

經前端 Nginx proxy 存取 API 時：

```sh
BASE_URL=http://localhost:8080
```

以下範例預設使用 `BASE_URL=http://localhost:8000`。健康檢查也可透過 proxy 執行：

```sh
curl --fail http://localhost:8080/api/health
```

所有帶 body 的 request 都使用 `Content-Type: application/json`。成功 response 依 endpoint 使用 200、201 或 204；204 response 沒有 body。

## 共通資料規則與錯誤

- 有 request schema 的 endpoint 拒絕 `null` 與未列在契約中的欄位；型別或欄位驗證失敗回傳 422。
- 商品與購物車 item 的 `product_id` 範圍為 1..9,223,372,036,854,775,807；超出範圍或非正整數回傳 422。
- `cart_id` 必須是至少 1 個字元的字串；空值回傳 422。
- 找不到購物車、商品或指定購物車商品項目回傳 404。
- 既有 `POST /api/orders` 遇未知商品回傳 400；購物車新增遇未知商品回傳 404。
- 購物車同一商品累加後超過 99 件，以及對空購物車結帳，回傳 400。

主要 4xx response 使用 FastAPI 的 `detail` 欄位：

| 狀態碼 | 情況 | `detail` |
| --- | --- | --- |
| 400 | 購物車累加後超過 99 件 | `每件商品最多可購買 99 件。` |
| 400 | 空購物車結帳 | `購物車是空的。` |
| 400 | `POST /api/orders` 的商品不存在 | `商品不存在，請重新整理商品列表。` |
| 404 | 商品或購物車不存在 | `商品不存在。` 或 `購物車不存在。` |
| 404 | 購物車商品項目不存在 | `購物車商品不存在。` |
| 422 | schema、path ID 或欄位限制不符 | FastAPI validation details |

刪除商品會同步移除購物車中的該商品；已建立訂單保存自己的商品快照，不因後續刪除或更新商品而改變。

## 健康檢查

| Method | Path | 成功 response |
| --- | --- | --- |
| GET | `/api/health` | 200，`{"status":"ok"}` |

## 商品 API

### 商品欄位

商品 response 包含 server 自動給定的 `id` 與下列六個欄位：

| 欄位 | 規格 |
| --- | --- |
| `id` | 正整數，範圍 1..9,223,372,036,854,775,807；由 server 自動給定，create request 不提供 |
| `name` | 字串，trim 後不可為空，長度 1..80 |
| `category` | 字串，trim 後不可為空，長度 1..40 |
| `price` | 嚴格整數，範圍 1..1,000,000,000，單位為 NTD |
| `icon` | 字串，trim 後不可為空，長度 1..32 |
| `color` | 字串，格式 `#RRGGBB`，十六進位字母不分大小寫 |
| `description` | 字串，長度最多 2,000；可為空字串 |

POST 與 PUT 必須提供全部六個可寫欄位：`name`、`category`、`price`、`icon`、`color`、`description`。PATCH 至少提供一個欄位。三種寫入 request 都拒絕 `id`、未知欄位與 `null`；文字欄位會先 trim。

### Endpoint

| Method | Path | 成功 response | 失敗狀態 |
| --- | --- | --- | --- |
| GET | `/api/products` | 200，商品陣列，依 `id` 排序 | — |
| GET | `/api/products/{id}` | 200，商品物件 | 商品不存在 404；path 不符 422 |
| POST | `/api/products` | 201，商品物件 | request 不符 422 |
| PUT | `/api/products/{id}` | 200，商品物件 | 商品不存在 404；request/path 不符 422 |
| PATCH | `/api/products/{id}` | 200，商品物件 | 商品不存在 404；request/path 不符 422 |
| DELETE | `/api/products/{id}` | 204，無 body | 商品不存在 404；path 不符 422 |

### curl 範例

```sh
# 取得商品陣列
curl "$BASE_URL/api/products"

# 取得單一商品
curl "$BASE_URL/api/products/1"

# 建立商品；id 由 server 回傳
curl -X POST "$BASE_URL/api/products" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "棉麻餐墊",
    "category": "居家生活",
    "price": 450,
    "icon": "▦",
    "color": "#D9E3D7",
    "description": "為餐桌添一點柔和色彩。"
  }'

# 將上一個 POST response 的 id 填入
PRODUCT_ID=replace-with-created-product-id

# PUT 必須提供全部六個欄位
curl -X PUT "$BASE_URL/api/products/$PRODUCT_ID" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "棉麻餐墊組",
    "category": "居家生活",
    "price": 520,
    "icon": "▦",
    "color": "#D9E3D7",
    "description": "為餐桌添一點柔和色彩。"
  }'

# PATCH 至少一欄；此例只調整價格
curl -X PATCH "$BASE_URL/api/products/$PRODUCT_ID" \
  -H 'Content-Type: application/json' \
  -d '{"price": 560}'

# 204 response 沒有 body
curl -i -X DELETE "$BASE_URL/api/products/$PRODUCT_ID"
```

## 匿名購物車 API

購物車不需要登入或 token。建立購物車不帶 request body，回傳格式為：

```json
{
  "id": "uuid4-hex",
  "items": [],
  "total": 0
}
```

每個 `items` line 包含完整商品欄位（`id`、`name`、`category`、`price`、`icon`、`color`、`description`），以及：

| 欄位 | 規格 |
| --- | --- |
| `product_id` | 正整數商品 ID，範圍 1..9,223,372,036,854,775,807 |
| `quantity` | 嚴格整數 1..99 |
| `line_total` | server 依目前商品價格計算的 `price * quantity` |

`total` 是所有 line 的 `line_total` 總和。商品價格被 PUT/PATCH 更新後，購物車讀取或變更時會以目前價格重算；商品刪除則移除該 line。加入既有 line 是累加數量，累加超過 99 回傳 400。

### Endpoint

| Method | Path | 成功 response | 失敗狀態 |
| --- | --- | --- | --- |
| POST | `/api/carts` | 201，`{id, items, total}` | — |
| GET | `/api/carts/{cart_id}` | 200，購物車 | cart 不存在 404；path 不符 422 |
| DELETE | `/api/carts/{cart_id}` | 204，無 body | cart 不存在 404；path 不符 422 |
| POST | `/api/carts/{cart_id}/items` | 200，購物車 | cart/product 不存在 404；累加超過 99 為 400；request/path 不符 422 |
| GET | `/api/carts/{cart_id}/items` | 200，items 陣列 | cart 不存在 404；path 不符 422 |
| PATCH | `/api/carts/{cart_id}/items/{product_id}` | 200，購物車 | cart/item 不存在 404；request/path 不符 422 |
| DELETE | `/api/carts/{cart_id}/items/{product_id}` | 200，購物車 | cart/item 不存在 404；path 不符 422 |
| DELETE | `/api/carts/{cart_id}/items` | 200，空購物車 | cart 不存在 404；path 不符 422 |

新增商品的 request 是 `{product_id, quantity}`；設定數量的 request 是 `{quantity}` 且為絕對數量。所有購物車 item request 的 `quantity` 必須是 1..99 的嚴格整數；移除商品使用 DELETE，不以 quantity 0 代替。

### curl 範例

```sh
# 建立購物車，不帶 body；將回傳的 id 填入 CART_ID
curl -X POST "$BASE_URL/api/carts"
CART_ID=replace-with-cart-id

# 取得購物車
curl "$BASE_URL/api/carts/$CART_ID"

# 加入商品；若已有同一商品則累加 quantity
curl -X POST "$BASE_URL/api/carts/$CART_ID/items" \
  -H 'Content-Type: application/json' \
  -d '{"product_id": 1, "quantity": 2}'

# 只取 items 陣列
curl "$BASE_URL/api/carts/$CART_ID/items"

# 設定絕對數量，不是累加
curl -X PATCH "$BASE_URL/api/carts/$CART_ID/items/1" \
  -H 'Content-Type: application/json' \
  -d '{"quantity": 3}'

# 移除單一商品；回傳更新後購物車
curl -X DELETE "$BASE_URL/api/carts/$CART_ID/items/1"

# 清空購物車；回傳空購物車
curl -X DELETE "$BASE_URL/api/carts/$CART_ID/items"

# 刪除購物車；204 無 body
curl -i -X DELETE "$BASE_URL/api/carts/$CART_ID"
```

## 訂單與結帳 API

### 既有 `POST /api/orders`

request 必須包含 `name`、`address`、`items`：

| 欄位 | 規格 |
| --- | --- |
| `name` | 字串，trim 後長度 1..80 |
| `address` | 字串，trim 後長度 5..300 |
| `items` | 陣列，長度 1..50；每筆為 `{product_id, quantity}` |
| `items[].product_id` | 嚴格正整數，範圍 1..9,223,372,036,854,775,807 |
| `items[].quantity` | 嚴格整數 1..99 |

相同商品在 request 中重複出現時會合併數量；合併後超過 99 件回傳 400。未知商品回傳 400。成功回傳 201 與訂單 response。訂單建立時保存商品資料快照，之後商品刪除或價格變更不會改寫既有訂單。

訂單 response 格式為：

```json
{
  "id": "order-id",
  "created_at": "2026-09-12T00:00:00+00:00",
  "total": 960,
  "items": [
    {
      "product_id": 1,
      "name": "晨光陶瓷杯",
      "price": 480,
      "quantity": 2
    }
  ]
}
```

response 的 `id` 與 `created_at` 是 server 產生的字串，`total` 是商品價格乘以數量後的總和；每個 order item 只有 `product_id`、`name`、`price`、`quantity`。配送姓名與地址會寫入 SQLite payload，但不出現在 `OrderResponse`。

```sh
curl -X POST "$BASE_URL/api/orders" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "測試顧客",
    "address": "台北市測試路 1 號",
    "items": [{"product_id": 1, "quantity": 2}]
  }'
```

### `POST /api/carts/{cart_id}/checkout`

request body 只有 `name` 與 `address`，限制與既有訂單 request 相同：`name` trim 後長度 1..80，`address` trim 後長度 5..300。成功回傳 201 與上述訂單 response，並在同一個 transaction 中清空購物車。未知 cart 回傳 404；空 cart 回傳 400；request schema 或 path 不符回傳 422。結帳明細與總額使用 server 當下的商品價格。

```sh
curl -X POST "$BASE_URL/api/carts/$CART_ID/checkout" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "測試顧客",
    "address": "台北市測試路 1 號"
  }'
```

## Demo 故障卡 API

`/api/demo-faults` 控制單一 backend process 內的示範故障，GET、POST、DELETE 的成功 response 格式相同：

```json
{
  "cards": [
    {"fault_id": "checkout_exception", "title": "結帳例外", "description": "..."},
    {"fault_id": "database_write_lock", "title": "資料庫寫入鎖", "description": "..."},
    {"fault_id": "checkout_delay", "title": "結帳延遲", "description": "..."}
  ],
  "active": null,
  "lease_seconds": 60,
  "delay_seconds": 10
}
```

`active` 啟用時為 `{fault_id, started_at, expires_at, remaining_seconds, status}`；`status` 可為 `activating`、`active` 或 `restoring`。`started_at` 與 `expires_at` 是 UTC ISO-8601 字串，`remaining_seconds` 由 server 的 monotonic clock 計算。`activating` 尚未取得 database lease，lease 的 60 秒期限會在成功取得後才開始；`restoring` 仍在釋放資源，完成前不能啟用另一張卡。

| Method | Endpoint | Request | 成功回應 | 錯誤 |
| --- | --- | --- | --- | --- |
| GET | `/api/demo-faults` | — | 200，故障卡 response | — |
| POST | `/api/demo-faults` | `{ "fault_id": "checkout_exception" }` | 200，故障卡 response | 未知 ID 或 body 多餘欄位 422；已有 active/activating/restoring 409；database lock 取得失敗 503 |
| DELETE | `/api/demo-faults` | 無 body | 200，`active: null` | — |

可啟用的 ID 固定為 `checkout_exception`、`database_write_lock`、`checkout_delay`。DELETE 是冪等操作，會喚醒等待中的 checkout；三種 lease 都會在 60 秒後自動清理。`checkout_exception` 會在 order insert 或 cart checkout 的 cart clear 寫入後、commit 前 raise `RuntimeError`，整筆 transaction rollback 並由 request middleware 記錄 traceback；`database_write_lock` 由專用 thread 持有自己的 SQLite connection 與 `BEGIN IMMEDIATE`，一般 checkout 仍使用 SQLite 10 秒 timeout；`checkout_delay` 在 transaction 前非阻塞等待 10 秒。

## 資料保存與前端整合

- SQLite 預設使用 `/data/shop.db`，由 Compose 的 `shop-data` named volume 保存。
- 商品資料只在資料庫第一次初始化時種子一次；重啟不會把已刪除商品重新種回來，既有 orders 會保留自己的商品快照。
- 商品寫入 API 不需 auth；本專案是本機購物 demo，沒有管理介面、登入權限或金流。
- 前端購物袋目前由瀏覽器 `localStorage` 保存，結帳使用既有 `POST /api/orders`；尚未整合新的 cart CRUD 或 cart checkout API。

## 驗證紀錄

在預設 `nightwatch-shop-web` project、frontend 8080 與 backend 8000 執行並通過：

- `make up COMPOSE=docker-compose`：成功建置並重建 `frontend`、`backend` 兩個 healthy containers。
- `make test COMPOSE=docker-compose`：10 tests 全部通過，涵蓋並發加購、並發 checkout、migration、CRUD/validation 與 order snapshot。
- `make smoke`：首頁與 Nginx proxy health 通過；透過 8080 實際驗證商品 POST/PATCH、購物車建立/加購/讀取時重算現價/checkout 清空與 DELETE 清理。
- 超大 ID 的商品 GET、cart body/path 與 orders request 均回傳 422；OpenAPI 含 10 個 paths 以及 `ProductResponse`、`CartResponse`、`OrderResponse` schemas。
- `uv lock --check` 通過，fresh `uv export --frozen --no-dev --no-hashes` 與 `requirements.txt` 依賴內容一致；UID 10001 runtime 與 `/data`、`/logs` 寫入權限通過檢查。
- `app.log` 的 UTC ISO/INFO startup、migration、HTTP 200/201/204/404/422、無 query path、INFO/ERROR traceback、每日 rollover 與 restart 後 log volume 保留通過檢查；isolated backend image 另實測 demo fault controls、兩種 rollback、delay 喚醒與 SQLite lock 500。

Chrome CDP 已以真實 DOM 驗證事件頁啟用/解除並取得 desktop/mobile 截圖；最終截圖 hash/visual review 尚待核對。`checkout_delay` 的 TTL 自動喚醒已驗證，`database_write_lock` 的 60 秒自動到期尚未單獨驗證。破壞性 `make clean CONFIRM=yes` 尚未執行。`make help` 與未帶 `CONFIRM=yes` 的 clean guard 已驗證。
