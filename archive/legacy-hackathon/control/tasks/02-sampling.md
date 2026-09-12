# control 02:取樣:每 5 秒把 Prometheus 的數字組成一張服務圖快照

## 為什麼要有這段
偵測、判讀、模型排查、報告全部建立在「每 5 秒一張快照」上。這段讓 control 開始看得到店面。

## 目標
- 對 Prometheus 與 Jaeger 取數:查詢句集中在一個地方、跟契約逐字一樣;一批查詢並行、整批有時間上限;來源掛掉時快照照樣產出,只是軸變空、來源狀態記下來。
- 每 5 秒一張快照:manifest 的 13 個節點都在,各自的軸、額外資訊、設定版本、對應的檢查;邊是「觀測到的」加「宣告的」,久沒流量要標出來;序號單調遞增。存活判斷照契約,分節點種類。
- 這段還沒有基線,所以健康狀態一律 unknown、趨勢一律 na。
- 最近 180 張放環形緩衝,可以拿最新、拿某時刻之前最近的一張、拿一段區間。
- 服務圖端點回最新一張或指定時刻那張;state 裡的 graph_now 變成真的;每張新快照推 SSE graph 事件;心跳照契約是 ping 事件。
- 啟動時印一行每個節點哪些軸有值,之後每 60 秒一行摘要,方便對名。

## 契約在哪
- `../contracts/METRICS.md` 全部(§3 的查詢句是這段的核心;§5 對名方法)
- `../contracts/API.md` §3(服務圖端點)§4(SSE graph、ping)§5(節點、邊、快照欄位、存活規則)
- schema:`snapshot` `node` `edge`;example:`snapshot.json`

## 怎麼算做完
- 起 `bun ../stubs/fake-stack.ts` 與 control(`NIGHTWATCH_BASELINE_AUTO=1 NIGHTWATCH_MODEL_OFFLINE=1`),12 秒內服務圖有 13 個節點、service 類的軸有數字、postgres 的飽和是空、shopper 有失敗原因,來源都 ok。
- 假 stack 對認不得的查詢句會回 400 並印出來:它的輸出乾淨才算過。
- 把假 Prometheus 砍掉 10 秒:來源 ok 變 false、age 在漲、軸變空;重啟後恢復。
- `bun ../contracts/check-live.ts` 全綠;事件流看得到 graph 與 ping 兩種事件。

## 測試
只寫契約邊界與主線:每條查詢句逐字等於 METRICS.md(這是跟店面的接縫,接 2 靠它);用假的 Prometheus 回應組出一張快照,13 個節點齊、沒流量的宣告邊有標出來;來源回 500 時軸為空但序號照加;環形上限與取值。

## 不做什麼
基線、分類、趨勢、歷史、就緒八項、log 接收、偵測、注入、模型。

## 接線點
接 1 的前半:前端 03 的服務圖吃你的服務圖端點與 SSE graph,畫出節點、邊與數字(顏色等下一段);dev-server 指到你的 3300,徽章要 ok 不能 stale。接 2 的準備:compose-min 起來後把 Prometheus 換成真的,看 catalog 的軸有沒有數字,空的就照 METRICS.md §5 對名。
