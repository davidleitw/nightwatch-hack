# Shop monitor 定義

本文件對應 `shop-web.config.json`。目前有 18 個觀測節點、16 條設定連線；節點是操作範圍，不是容器或獨立服務實例。新增 ID 為固定值，不含商品、購物車、訂單或 request ID。

Console 預設只顯示六個主要流程節點與所有警告／異常節點，可勾選「全部觀測點」。這只影響畫面，API 與調查仍使用全部 18 個節點。

## 指標與健康判定

- `traffic`：最近 60 秒完成的呼叫數 ÷ 60，單位 requests/second；DB monitor 則為 operations/second。不是窗口內總筆數。
- `errors`：失敗完成數 ÷ 全部完成數，值域 0–1。沒有完成樣本時為 null。
- `p95_ms`：完成呼叫耗時的 nearest-rank P95，單位毫秒；包含失敗樣本。沒有耗時樣本時為 null。
- `saturation`：尚未量測，固定 null。
- `alive`：60 秒內有任何事件，不是程序或容器存活探測。没有事件時為 false，traffic 為 null。
- `status`：沒有完成樣本為 unknown；全部失敗為 failing；部分失敗為 warning；全部成功但達到延遲門檻為 warning；其餘為 ok。延遲不會單獨升為 failing。
- `assessment`：未經調查判定時為 unassessed，與上述健康狀態分開。
- 只在呼叫完成後判斷延遲；尚在等待的請求不會提前觸發耗時警告。沒有後續流量時，樣本在 60 秒後過期。

HTTP 5xx、連線失敗、逾時、無效下游資料及未預期例外計為系統失敗。預期 HTTP 4xx（空購物車、商品不存在、操作衝突、輸入錯誤）不計系統失敗，但若已進入監測範圍，仍計流量與耗時。FastAPI 在 handler 前拒絕的驗證錯誤不一定產生 handler monitor 樣本。正常回傳的 5xx Response 也會被分類為失敗。

## 監測點

`node_id` 是下表 monitor ID 將 `.` 換成 `-`；唯一例外是既有 `shop.db.query` 對應 `shop-db`。新增門檻是 demo 的初始設定，尚未按負載基線校準。除商品列表既有 20 個樣本門檻外，配置延遲警告的節點都只需 1 個樣本。

| Monitor ID | 程序／掛載位置 | 一個樣本代表什麼 | P95 警告門檻 |
| --- | --- | --- | --- |
| shop.health | Gateway `health()` | 經 Gateway 查 Order DB 健康；不代表 Catalog／Cart 全部健康 | 未設定 |
| shop.products | Gateway `products()` | 一次商品列表請求，包含 Catalog 往返 | 500ms，至少 20 筆 |
| shop.db.query | Order `_health_sync()` | 一次健康檢查 DB 查詢，不是所有業務讀取 | 未設定 |
| shop.checkout.request | Gateway middleware | 一次結帳 HTTP 請求，包含下游等待及故障延遲 | 1000ms |
| shop.checkout.logic | Order `_checkout()` | 一次結帳邏輯，含查價、保留、DB 與補償；不含外層故障延遲 | 1000ms |
| shop.db.write | Order `CheckoutConnection` | 有 invocation context 的寫入 SQL／transaction 結束；一次結帳有多筆 | 500ms |
| shop.catalog.read | Catalog `products()`、`get_product()` | 一次列表或單一商品讀取，包含本機 DB 與資料轉換 | 500ms |
| shop.catalog.lookup | Catalog `lookup_products()` | 一次內部批次商品查詢；不是每商品一筆 | 500ms |
| shop.cart.read | Cart `get_cart()`、`get_cart_items()` | 一次購物車讀取，包含 Catalog 查詢及必要快取更新 | 1000ms |
| shop.cart.mutate | Cart 公開 create/delete/add/set/remove/clear handlers | 一次購物車修改，包含回應組裝與下游查詢；不含結帳端點 | 1000ms |
| shop.cart.catalog.lookup | Cart `_lookup_catalog()` | 一次完整商品查詢，可分成多批 HTTP；包含解析與協定驗證 | 500ms |
| shop.cart.prepare | Cart `prepare_checkout()` | 一次結帳內容準備與保留，含 DB 與 Catalog 查詢 | 1000ms |
| shop.cart.complete | Cart `complete_checkout()` | 一次完成已保留結帳的請求，含清理 cart items／操作狀態 | 500ms |
| shop.cart.abort | Cart `abort_checkout()` | 一次解除或記錄取消保留的請求；成功補償算成功 | 500ms |
| shop.order.cart.prepare | Order `_prepare_cart()` | 一次對 Cart prepare 的往返與回應驗證 | 1000ms |
| shop.order.catalog.lookup | Order `_lookup_products()` | 一次對 Catalog 查價的往返與回應驗證 | 500ms |
| shop.order.cart.complete | Order `_complete_cart()` | 一次 complete 往返，包含前景與背景補償呼叫 | 1000ms |
| shop.order.cart.abort | Order `_abort_cart()` | 一次 abort 往返，包含前景與背景補償呼叫 | 1000ms |

讀取與查詢 monitor 量的是整個操作；Catalog／Cart 本機 SQL 尚未有獨立 DB 節點。Catalog 商品新增／修改／刪除也尚未單獨量測。

## 連線與關聯

- 商品列表：`shop.products → shop.catalog.read`。
- 購物車 read／mutate／prepare：`→ shop.cart.catalog.lookup → shop.catalog.lookup`。
- 結帳：`shop.checkout.request → shop.checkout.logic`；logic 呼叫 `shop.order.cart.prepare`、`shop.order.catalog.lookup`、`shop.db.write`、`shop.order.cart.complete` 或 `shop.order.cart.abort`。
- Order 的 prepare／complete／abort 分別連到 Cart 同名操作；Order lookup 連到 Catalog lookup。
- 健康檢查：`shop.health → shop.db.query`。

設定連線代表可能的呼叫關係，不保證每次都執行，也不代表當下有流量。Edge 的 rps/errors/p95_ms 仍為 null、observed=false。父子觀測耗時會重疊，不可把各節點流量或耗時相加當成總請求數／總耗時。`shop.catalog.read` 也接受未被 Gateway 列表 monitor 包住的單一商品讀取。

HTTP client 將當前 invocation ID 放入內部 `X-Nightwatch-Parent-Invocation`；Cart／Catalog／Order 接收後建立子 invocation。Gateway 不採信外部傳入的 parent header；此欄位只用來關聯，不作為授權或去重。HTTP executor 明確傳遞 context；Order DB executor 沿用既有 context 傳遞。

沒有前景父 invocation 的背景補償仍會記錄自己的 complete／abort 呼叫。這不表示補償佇列已清空；尚未量測 pending 筆數、最老 operation 年齡、活躍請求數或 executor 排隊時間。

## 部署與驗證

四個 Python 服務都需要掛載 `control/monitor` 與可寫的共用 `control/tmp`；Guard Room 唯讀取得同一份 JSONL。新增 ID 必須同時加入 graph config；修改後需重啟 Guard Room，服務程式改動需重建 Shop 映像。

整合測試 `shop-web/backend/tests/test_api.py` 會在臨時資料庫及四個隔離服務程序中檢查：既有 API 行為、跨服務 parent ID、結帳例外後 abort、成功結帳 complete、4xx 不算系統失敗，以及真實 Catalog SQL 失敗的 500 沿 Gateway 正確分類。這些測試不使用正式 Shop 資料。
