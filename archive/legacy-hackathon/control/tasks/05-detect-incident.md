# control 05:偵測、事故 journal、read model、SSE cursor、模型離線結案

## 為什麼要有這段
這段讓 control 從「看得到」變成「會叫」:量測連續超標就開一件事故,把前後的快照釘住,之後模型排查、稽核、報告都以這件事故為單位。也讓前端斷線重連不會漏掉或重複任何事件。

## 目標
- 偵測器每張快照跑契約的三條規則,同一條連續三張才觸發;基線沒好不跑;結案後關閉直到換輪。觸發就開事故,記下規則、訊號、中文摘要,把偵測前的 120 張快照釘進事故,之後每張新快照也追加,時間一律用相對偵測時刻的秒數。
- 每件事故是一條 append-only 的 journal,每筆事件套進 read model 得到新的版本號,同時落檔;上一段的故障實例變化、回合開始、基線凍結也寫進 journal(沒有事故時掛在回合層級)。
- read model 是契約的攤平形狀,整體狀態與單一事故端點回同一份;不存在的事故 404。
- SSE 每筆 journal 事件推一個 commit,帶前一版與新版的版本號與改到的欄位;帶 cursor 重連時先送整份 state 再補送之後的 commit,回合不同就只送 state,格式錯就拒絕。
- 模型不可用時,事故直接以 unresolved 結案並說明原因;模型可用的路徑是下一段。
- 上一段留的判斷點接上:事故進行中不能注入、不能無 force 還原;基線期沒有事故時不能換輪(開發旗標拿掉);結案後提示換成「已結案,按開始下一輪」,換輪後事故清空、偵測重開。

## 契約在哪
- `../contracts/API.md` §3(incidents 端點與錯誤碼)§4(SSE incident、cursor 規則)§5(偵測規則)§6(read model、journal 事件)
- schema:`incident-commit`;example:`state.json` 的 incident;fixture:`catalog_pool_leak/events.jsonl` 看 commit 長相與事件順序

## 怎麼算做完
- 假 stack + control(模型離線),注入 catalog 卡,約 110 秒後 state 裡有事故:phase 與 outcome 都是 unresolved、規則是 R1、釘住數量 ≥ 20;落檔的 journal 事件順序合理。
- 事故進行中或結案未換輪時再注入被 409;帶當前回合的 cursor 重連會補送之後的 commit,帶別的回合只送 state。
- 換輪後事故為 null;`bun ../contracts/check-live.ts <control> <事故 id>` 的事故端點通過(時間軸、報告端點之後的段才有,404 可接受並在回報註明)。

## 測試
只寫契約邊界與主線:偵測器的連續三張、中間插一張正常就重數、結案後不再觸發;journal 套進 read model 後 phase、outcome、版本號正確,每筆 commit 的前一版等於上一筆的新版;cursor 三種情況;釘住在環形夠與不夠時的數量與 t。

## 不做什麼
模型、稽核、提案、動作、驗證、報告端點。

## 接線點
接 3(完整,協調者主持):假 stack + 你的 control + dev-server,前端 02 與 04:卡片頁按注入、切到舞台頁,約 110 秒後出現「偵測到異常」、活動流依序出現偵測、釘住、開始排查、結案;把 control 砍掉再起,前端帶 cursor 重連後活動流筆數不重複、不倒退,這是 SSE 契約最容易對不上的地方。真店面版:compose-min 加上店面 05、07 後做同一套動作,記下實際偵測秒數交給協調者回填 cards。
