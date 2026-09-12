# 店面 06:出貨工人把訂單迴圈關起來(第三張故障卡的機制)

## 為什麼要有這段
沒有工人,訂單永遠停在 paid,顧客等不到出貨、demo 的迴圈關不起來。第三張卡「工人變慢、訂單堆積」的症狀在佇列深度與顧客逾時,只有這段能產生。

## 目標
- fulfillment 沒有對外路由;背景兩個工人並行撈 paid 的訂單處理成 fulfilled,兩個工人不會撈到同一筆。
- 每筆處理在一個 consumer span 裡,接在訂單存的 trace 上,讓一筆訂單從結帳到出貨是同一條 trace。
- 處理一筆要花的時間由旋鈕 `worker.process_ms` 決定,改了下一筆生效;拉長就堆積,推回去要能排空。
- 佇列的深度、最舊訂單的年齡、處理筆數、處理時間以契約規定的名字報出去。

## 契約在哪
- `../contracts/SHOP-INTERNAL.md` §2(fulfillment 那列)、§3(卡 C 那行)、§6(fulfillment)
- `../contracts/METRICS.md` §2.1(consumer span)、§2.2(`shop.queue.*`)

## 怎麼算做完
- 前一段的服務加上 fulfillment,下幾單,訂單頁 2 秒內從 paid 變 fulfilled;資料庫裡 paid 幾乎是 0。
- 把 `worker.process_ms` 推到 6000,連下 10 單,paid 開始堆、沒有 ERROR;推回出廠值後排空。記下排空花幾秒。

## 測試
只寫契約邊界與主線。一定要有:工人對假的資料庫撈一筆、睡、改狀態、計數加一;旋鈕改了下一筆生效;consumer span 的 trace id 等於訂單裡存的。

## 不做什麼
合成顧客、shipping、稽核磁碟。

## 接線點
compose-min 加上 fulfillment:Jaeger 裡一筆訂單的 trace 多出 consumer span;Prometheus 有四個 `shop_queue_*`;服務圖多 fulfillment→postgres。control 的圖上 orders-queue 與 fulfillment 這時才有數字。卡 C 的顧客端症狀要等 07。
