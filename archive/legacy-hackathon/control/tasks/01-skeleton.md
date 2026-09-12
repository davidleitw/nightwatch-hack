# control 01:骨架、設定載入、卡片目錄、狀態投影的殼

## 為什麼要有這段
control 是 demo 的大腦,後面每一段都掛在這個骨架上。先把「讀設定、對外的殼、錯誤的形狀」立起來,前端從第一天就有真的 control 可以接。

## 目標
- 一個執行檔,啟動時載入 manifest 與 cards 並驗證一致性(節點、邊、動作、卡片互相指得到);壞檔案用一行中文說哪裡壞,然後拒絕啟動。
- 曲線引擎是純函式:給曲線定義與秒數就得到那一刻的值,四種曲線照契約公式,並能產出 24 點的預覽。
- 公開埠有健康、就緒、卡片目錄、能力、整體狀態、故障實例、服務圖、SSE 事件流這幾個入口;內容可以先是殼(空快照、假回合、沒有事故),但形狀要過 schema。卡片目錄不能洩漏答案。
- 所有錯誤回應都是契約的 envelope;沒實作的路徑 404、方法錯 405;非 API 路徑回一頁「前端未 build」。
- SSE 一連上先給整份 state,之後每 2 秒一個 ping 事件;沒有回合 id 的 cursor 要拒絕。
- 內部埠先能收 log 與 Grafana 告警(先回應、不處理);做一個可重用的冪等請求機制,之後所有 POST 都用它。

## 契約在哪
- `../contracts/API.md` §1–§4(名詞、環境變數、端點、state 與 SSE)、§8(能力要列出的工具清單)
- `../contracts/SHOP-INTERNAL.md` §3(曲線公式)
- `../contracts/manifest.yaml`、`cards.yaml`;schema:`state` `readiness` `snapshot` `fault-catalog` `faults`;examples:`state.json` `fault-catalog.json`

## 怎麼算做完
- `go build ./... && go vet ./... && go test ./...` 過,`CGO_ENABLED=0 GOOS=linux` 交叉編譯過。
- 用 `NIGHTWATCH_MODEL_OFFLINE=1` 起在 3300/3301,`bun ../contracts/check-live.ts http://127.0.0.1:3300` 全綠。
- 把 cards 複製一份改壞(例如租約太短)再啟動:印中文錯誤、exit 1。
- `curl -N` 事件流看得到 state 事件與每 2 秒的 ping 事件。

## 測試
只寫契約邊界與主線:四種曲線在起點、中點、超過長度的值與 24 點預覽;載入驗證一個好例子、三個壞例子;冪等機制同 id 同 body 回同結果、同 id 不同 body 回衝突。

## 不做什麼
Prometheus 查詢、快照、基線、偵測、注入、模型、稽核、動作。

## 接線點
接 0:前端 01 的空殼接你的 control(假 stack 不用起),徽章 ok、看得到回合 id 與基線狀態。
