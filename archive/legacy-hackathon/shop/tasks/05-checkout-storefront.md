# 店面 05:結帳、訂單、店面網頁,一筆訂單從按鈕走到資料庫

## 為什麼要有這段
到這段顧客才真的能買東西。checkout 串起 cart、catalog、payment、postgres,一筆結帳是一條 trace,服務圖上大部份的邊從這裡來;錯誤頁要看得出是哪一層壞,demo 時觀眾才看得懂故障。

## 目標
- checkout 照契約的順序串各服務:購物車失敗重試一次,付款被拒原樣回 402,其他失敗回 502 並標出是哪一步壞;沒有設定出貨服務位址時跳過出貨那一步(出貨是第 08 段)。
- 訂單與明細在同一個交易寫進 postgres,並把當下的 trace 資訊存進訂單,讓之後的出貨工人接在同一條 trace 上;寫訂單同時視為「入佇列」,要有契約規定的 producer span 與計數。
- storefront 是對外的網頁與 API,自己不存東西:以 cookie 認顧客,API 形狀照契約,網頁純 HTML、內嵌 CSS、不外連;訂單頁自動刷新直到出貨完成。
- 結帳失敗的網頁看得出是目錄、付款、購物車還是逾時的問題。
- 服務間全部用第一段的共用 client,一筆結帳從 storefront 到 postgres 是同一條 trace。

## 契約在哪
- `../contracts/SHOP-INTERNAL.md` §5(對外 API、錯誤頁)、§6(checkout 順序、資料表;內部端點是建議值)、§8(`SHOP_*_URL`)
- `../contracts/METRICS.md` §2.1(`orders publish` producer span)、§2.2(`shop.queue.enqueued_total`)

## 怎麼算做完
- 五個服務在主機上各開一個 port,用 curl 帶 cookie 走完:看商品、加購物車、結帳拿到訂單編號、查訂單是 paid;瀏覽器走一遍同樣流程。
- 付款失敗率推到 1,結帳回 402 並標 payment;切備援後再結帳成功。catalog 漏水推快,結帳回 502 並標 catalog,網頁顯示目錄不可用。

## 測試
只寫契約邊界與主線。一定要有:checkout 對假的下游(正常、購物車重試成功、重試失敗、付款 402、沒設出貨位址不打出貨);storefront 把下游的錯誤原樣往外回。

## 不做什麼
合成顧客、出貨工人、shipping、稽核磁碟。

## 接線點
compose-min 加上 cart、payment、checkout、storefront,人在瀏覽器下一單:Jaeger 裡一筆結帳是一條 trace、有 producer span;服務圖指標有 storefront→checkout 與 checkout→cart/catalog/payment 四條邊(對照 `manifest.yaml`)。「接 3」還要等 07。
