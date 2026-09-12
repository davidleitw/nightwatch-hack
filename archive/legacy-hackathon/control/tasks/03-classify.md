# control 03:判讀:基線、偏離、健康分類、趨勢、歷史、就緒、log 接收

## 為什麼要有這段
有了數字還要有「正常是什麼」。這段讓每個節點會變色、會說趨勢,讓「可以按故障卡」這個狀態有依據,也把店面的 log 收進來給之後的排查用。

## 目標
- 基線、偏離帶、偏離時間、健康分類、趨勢、主軸是同一組純函式,之後偵測、稽核、歷史都用它;基線在回合就緒後收 120 秒(24 張)凍結,凍結前一律 unknown / na。
- 回合狀態機:starting → collecting → ready,state 裡看得到收了幾秒;有開發旗標可以跳過前置條件直接開始收。
- 就緒檢查有八項,每項有 id、狀態、中文說明,全部 ok 才算 ready;下一步提示依狀態換字(等服務、收基線中、可以按卡、模型不可用只偵測)。
- log 接收:內部埠收 OTLP JSON 的 log,存在有上限的環形裡,可以按服務、嚴重度、時間、關鍵字查;快照裡「這個服務有沒有 log」與 log 來源狀態變成真的;留一個開發用的讀回端點。
- 歷史端點:給節點與視窗,回降採樣後的序列,帶基線與帶寬;視窗或節點不合法要拒絕。
- 就緒任一項變化推 SSE readiness 事件。
- 店面早期只有部份服務在跑:沒數字的節點分類 unknown、就緒只看目前存在的服務,不能因為缺節點整個 not ready 或當掉。

## 契約在哪
- `../contracts/API.md` §3(歷史、就緒端點)§4(SSE readiness)§5(基線、偏離帶、分類、趨勢、降採樣的定義都在這)
- `../contracts/METRICS.md` §2.4(log 欄位)
- schema:`history` `readiness`;examples:`history.json` `readiness.json`

## 怎麼算做完
- 假 stack + control:12 秒內就緒七項 ok、基線那項在數;120 秒後 ready、提示「可以按故障卡」;節點全部 ok、趨勢 flat。
- 用假店面自己的設定端點把 catalog 弄壞(漏水 6/分),90 秒內 catalog 的飽和上升、狀態變 warning 或 failing、趨勢 rising、主軸是 saturation;弄回來後恢復。
- 歷史端點對 catalog 回 26 點以內且帶基線與帶寬;不合法的視窗被拒。
- 假 stack 送的 log 從讀回端點查得到;事件流看得到 readiness 事件;`check-live.ts` 全綠。

## 測試
只寫契約邊界與主線:純函式對固定序列的基線、帶寬、偏離時間、四種分類、趨勢與主軸優先序;餵 24 張快照後基線凍結且不再變;log 的最小 OTLP 解析與查詢篩選。

## 不做什麼
偵測、事故、注入(上面是用假店面自己的端點)、模型。

## 接線點
接 1(完整):假 stack + 你的 control + dev-server,舞台頁 13 個節點全綠,弄壞 catalog 後 90 秒內它與 checkout 變色;看不到顏色先看 SSE graph 裡的 status 有沒有變,分清是判讀還是畫面。接 2:compose-min 起來後換真 Prometheus,先照 METRICS.md §5 對名,再對真 catalog 開漏水,看飽和與狀態會不會變。
