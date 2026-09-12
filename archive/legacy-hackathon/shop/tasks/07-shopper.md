# 店面 07:合成顧客一直下單,偵測訊號從這裡來

## 為什麼要有這段
control 的偵測規則 R1 看的是「顧客結果」的比例;沒有持續的顧客流量,服務圖沒有基線、故障也沒有症狀。從這段起每張卡都有顧客端的症狀。

## 目標
- shopper 沒有對外路由;啟動後 8 個顧客各自帶自己的 cookie,照契約的迴圈一直買:看、選、加、結帳、等出貨、休息;整體至少每秒一單。顧客數可以用環境變數改。
- 每一單的結果歸成契約的四種,以契約規定的計數與延遲量測報出去;正在等的顧客數也報。
- shopper 自己的 HTTP client 也是共用 client,服務圖上才有 shopper→storefront 這條邊。
- 每 10 秒印一行摘要(幾單、各結果幾筆),人看 stdout 就知道店面現在健不健康。storefront 連不上時不崩,持續重試並記為錯誤。

## 契約在哪
- `../contracts/SHOP-INTERNAL.md` §5(對外 API)、§7(迴圈與四種結果)
- `../contracts/METRICS.md` §2.2(`shop.shopper.*`)、§3(R1 那句查詢用到 `outcome` 怎麼分)

## 怎麼算做完
- 06 為止的服務加上 shopper(顧客數先開 4),摘要裡 ok 佔絕大多數、沒有錯誤,資料庫的 fulfilled 一直在漲。
- 三張卡各推一次再推回去:catalog 漏水推快約 10 秒後摘要出現 `http_error`;付款失敗率推到 1 出現 `payment_declined`;工人推慢約 40 秒後出現 `fulfillment_timeout`。記下每張卡從推旋鈕到症狀出現的秒數,以及平常的 ok 比例。

## 測試
只寫契約邊界與主線。一定要有:對假的 storefront 把四種結果各走一次。

## 不做什麼
shipping、稽核磁碟。

## 接線點
做完就是「接 3」的店面端:compose-min 八個服務減 shipping,shopper 一直在下單,Prometheus 有三個 `shop_shopper_*`、服務圖多 shopper→storefront。協調者用 control 按卡 A,約 2 分鐘後三條偵測規則陸續亮、舞台頁出現「偵測到異常」。回報的 ok 比例與症狀秒數協調者拿去核對偵測門檻。
