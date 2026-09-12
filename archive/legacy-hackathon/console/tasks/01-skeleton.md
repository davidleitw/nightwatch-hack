# console 01:工具鏈、型別、資料層(即時 + 離線回放)、三頁空殼

## 為什麼要有這段
之後每一頁都只認「一份畫面用的狀態」,不直接碰網路。這段把資料怎麼進來、怎麼回放、怎麼斷線重連一次做對,後面的頁面就只剩畫圖。

## 目標
- `bun run build` 產出一個不外連任何東西的正式版靜態頁(含離線回放要用的錄影檔);build 完印產物大小,主程式不超過 300 KB。
- 型別檔手寫,欄位名與列舉值跟契約的 schema 一字不差。
- 兩種資料來源,畫面只認同一個介面:即時(先拉整份狀態,再接推播;每 2 秒的心跳算有動靜;6 秒沒動靜顯示 stale、15 秒顯示 disconnected;斷線用 1、2、4、8 秒退避重連;重連能接上斷掉那一刻之後的事故變更,換輪時整份重拉)與離線回放(網址指定哪一輪、第幾秒,能暫停、跳到任一秒、往回跳)。
- 一份 store 餵 React;沒變化時不重繪。
- 三頁只有殼:頁名、連線狀態徽章、下一步提示、回合與基線狀態、事故階段、事故變更筆數;hash 路由切頁不重載;頁面頂端有全域錯誤條,任何請求失敗或壞資料都顯示錯誤碼與中文訊息,不藏。

## 契約在哪
- `../contracts/API.md` §1 名詞、§3 錯誤碼、§4 state 與 SSE(含 cursor 規則與心跳)、§5 節點與歷史、§9 fixture 格式
- `../contracts/schemas/{state,snapshot,node,edge,incident-commit,faults,readiness,sse-line}.schema.json`、`../contracts/examples/state.json`
- `../contracts/fixtures/catalog_pool_leak/`(六個檔)

## 怎麼算做完
- `bun test && bun run build` 綠。
- 瀏覽器裡開兩種來源:對 `../stubs/mock-control.ts` + `../stubs/dev-server.ts` 看事故階段隨播放前進、筆數不重複;砍掉假 control 徽章依序變 stale、disconnected,重啟後回 ok 且筆數不倒退。
- `?fixture=catalog_pool_leak&t=<秒>` 三個不同秒數的階段跟 `events.jsonl` 一致,往回跳也對。
- 截圖存 `.shots/01-stage-live.png`、`.shots/01-stage-fixture.png`。

## 測試
只寫契約邊界與主線:reducer 對三種事件的替換/合併/去重;cursor 什麼時候帶、什麼時候不帶;離線回放往回跳狀態跟著回去。

## 不做什麼
真正的卡片、服務圖、活動流、時間軸、批准按鈕。

## 接線點
接 0:對真 control 01(假 stack)開舞台頁,徽章 ok、回合 id、基線 collecting;對不上的列給協調者。
