# 店面 02:商品目錄接上 postgres,連線池的狀況看得到

## 為什麼要有這段
第一張故障卡是「連線池慢慢漏光」,症狀要從 postgres 節點的飽和度看出來。這段先把池、目錄端點、池的量測做對;漏水本身下一段才加。

## 目標
- catalog 啟動時自己建表、種 12 件商品;postgres 還沒好就每 2 秒重試,好之前不算就緒;重複啟動不會重複種。
- 目錄端點每次都真的查資料庫,不快取,否則之後池滿沒有症狀。
- 連線池上限 12 條、拿連線最多等 2 秒;拿連線這一步有自己的 span,逾時時 span 標錯、請求回 500、寫契約規定的那行 ERROR log。這段還沒有東西會把池拿光,但這條路徑要先通、要有測試。
- 池的使用量、上限、等待次數以契約規定的名字報出去。
- 池要留一個「借走不還、之後一次全還」的口,給下一段的漏水器用。

## 契約在哪
- `../contracts/SHOP-INTERNAL.md` §2(catalog 那列)、§6(資料表;端點是建議值)
- `../contracts/METRICS.md` §2.1(`db.system`、`catalog.db.acquire`)、§2.2(`shop.db.pool.*`)、§2.4(關鍵 log)

## 怎麼算做完
- 有 Docker:起一個 postgres,catalog 就緒、商品 12 件、單件可查、不存在的回 404;重啟 catalog 商品還是 12 件;postgres 停掉再起 catalog,每 2 秒重試、就緒端點 503,postgres 回來後 200。
- 沒 Docker:測試、vet、build 過,回報寫明「需要 postgres 才能驗的部份」。

## 測試
只寫契約邊界與主線。一定要有:拿連線逾時的錯誤路徑(對假的池);種資料的冪等。

## 不做什麼
漏水、購物車、結帳、店面網頁、合成顧客。

## 接線點
做完協調者可以起 compose-min(postgres + collector + prometheus + catalog),用 `METRICS.md` §5 的方法對名:池的三個量測、`shop_config_revision`、`catalog.db.acquire` 的 span 都要在;control B02 的圖上 catalog 與 postgres 兩個節點有數字。這是「接 2」的前半。
