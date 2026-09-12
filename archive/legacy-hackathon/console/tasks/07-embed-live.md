# console 07:收尾:嵌進 control、接真 control 走一輪、投影可讀性、斷網

## 為什麼要有這段
demo 當天只開 control 一個網址,console 要住在它裡面;投影機上要看得到字;現場沒網路。這段把這三件事關起來,並用真 control 走一輪找出跟契約不符的地方。

## 目標
- `console/` 能被 control 以 Go 模組方式嵌入(build 產物打進去),`bun run build` 之後在 `console/` 下 `go build ./...` 要過。
- 用協調者給的真 control 網址開三頁走一輪:注入 → 排查與批准 → 報告;把跟契約不符的地方列成一張表交給協調者,不在前端補算。
- 1920×1080 與 1440×900 兩種解析度量實際字級,達到 `BRIEF.md` 的下限。
- 尊重使用者關動畫的設定;關 Wi-Fi 開三頁沒有任何錯誤與外部請求。
- 對話頁籤接上真 control 的對話端點,回覆與工具呼叫摘要看得到,失敗原樣印錯誤碼。
- 每個階段、每個禁用原因、每種連線狀態、每種錯誤碼都有中文,任何地方不露出 undefined 或原始 JSON。

## 契約在哪
- `../contracts/API.md` §2 埠與環境變數、§3 chat 端點與錯誤碼、§10 版本字串
- `BRIEF.md` 技術限制(字級下限、不外連)

## 怎麼算做完
- `bun test && bun run build` 綠。
- 對真 control 三頁截圖存 `.shots/07-live-cards.png`、`.shots/07-live-stage.png`、`.shots/07-live-report.png`,兩種解析度的量測數字寫在回報裡。
- 關 Wi-Fi 開三頁,開發者工具的 console 與 network 都乾淨。

## 測試
不新增;既有的要繼續綠。

## 不做什麼
排練開關、demo 模式、加分功能。

## 接線點
全 compose:control 嵌進 `dist/` 後,`deploy/up.sh` 起來的 control 網址直接就是 console;這段交出的「不符契約」表是這輪最後一次契約修正的來源。
