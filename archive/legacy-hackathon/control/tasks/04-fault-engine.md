# control 04:故障引擎:注入、曲線推送、租約、還原、換輪

## 為什麼要有這段
demo 的每一輪都從「按一張卡」開始。這段讓 control 能把店面照卡片的曲線慢慢弄壞、時間到或按還原就推回出廠值、然後開下一輪。

## 目標
- 注入:按卡建實例;准入條件(卡存在、回合就緒、沒有事故進行中、沒有其他實例、租約長度合法)不符時回契約的錯誤碼;通過就對每顆旋鈕的服務送出曲線起點的值;請求冪等。「沒有事故」這個條件這段先留判斷點、永遠通過。
- 曲線推送:每 2 秒算現值,值有變才送,只送這張卡自己的旋鈕(靠店面的合併語意);實例上看得到進度、經過秒數、剩餘租約;店面連續失聯要標成 unknown。
- 租約到期或按還原:對受影響的服務送**完整**出廠表,狀態走 restoring 到 expired 或 restored;事故進行中且沒 force 的還原要拒絕。
- 實例清單在端點、state、SSE faults 三處一致;狀態變化立刻推,進度定期推。
- 換輪:先全部還原、等到沒有 failing 節點(有上限,超時算失敗並說是哪個節點)、換新的回合 id、基線重收;有操作端點可以看進度;換輪推 SSE run 事件;基線期沒有事故時按換輪要拒絕。這段沒有事故,用開發旗標繞過來測換輪本身。
- 就緒檢查的 fault_clear 與下一步提示反映「故障進行中:哪張卡」;每次狀態變化 log 一行中文。

## 契約在哪
- `../contracts/API.md` §2(開發旗標)§3(faults、rounds 的端點與錯誤碼)§4(SSE faults、run)
- `../contracts/SHOP-INTERNAL.md` §1(合併語意)§2(出廠表)§3(曲線、五張卡動的旋鈕)§4(動作步驟、位址改寫)
- `../contracts/cards.yaml`;schema:`faults`;example:`faults.json`

## 怎麼算做完
- 假 stack + control(加 `NIGHTWATCH_ROUNDS_ALWAYS=1`),就緒後注入 catalog 卡:202;同 request_id 再送回同一個實例;第二張卡被 409;不存在的卡 404;假 catalog 的設定變 v2;就緒的 fault_clear 變 failed。
- 全部還原後假 catalog 回 v1。注入 payment 卡,20 秒後假 payment 的兩顆旋鈕都比起點大、再 10 秒又更大。
- 換輪:202、新回合 id、假 payment 回 v1、就緒回到基線收集中;連著的事件流收到 run 事件。
- `check-live.ts` 全綠。

## 測試
只寫契約邊界與主線:五種准入失敗與冪等;用假的接收端驗 linear 曲線 10 秒內遞增、值沒變不送、租約到期送的是完整出廠表;換輪在有 failing 與沒 failing 兩種情況的結果。

## 不做什麼
偵測、事故、journal、SSE incident 與 cursor、模型。

## 接線點
接 3 的前半:前端 02 的故障卡頁對你的 control 按注入、看進度、還原、開下一輪;按了沒反應先 curl 同一個端點分清是誰的問題。接 2 的延伸:對 compose-min 的真 catalog 注入,看它的設定變 v2、Prometheus 出現新的 revision。
