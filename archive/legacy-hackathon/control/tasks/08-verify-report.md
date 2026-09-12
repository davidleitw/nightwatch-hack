# control 08:驗證窗、結局、報告與時間軸、錄 fixture

## 為什麼要有這段
修完要證明真的好了,而且要留下可以回放的證據。這段決定一件事故最後算什麼結局,並產出報告頁要吃的東西與離線回放用的錄影。

## 目標
- 驗證:執行完先等系統穩(有上限),再連續兩個 20 秒窗口,每張快照都要過通用謂詞(顧客的錯誤與延遲回到帶內、沒有 failing 節點)與卡片自己的謂詞;每個窗口寫事件並記觀察值。兩窗都過就 recovered(只繞過的動作是 recovered_mitigated,並列出殘留的節點);任一窗不過是 verification_failed。
- 其他結局:租約在事故中到期要先還原再依階段給 expired_before_approval 或 expired_before_execution,之後的窗口標為安全還原、不計成功;還原失敗有自己的結局。前幾段的結局維持。
- 結案時 read model 填卡片 id、偵測關閉、提示換成「已結案,按開始下一輪」。
- 報告與時間軸在結案時算一次並落檔:每節點每軸的偏離時間、注入的真相(注入時刻、曲線、卡片的答案)、模型說法與真相的差值、早期跡象那張快照、三段時長;系統時間軸從 journal 映射,AI 時間軸帶稽核的接受或駁回。
- 端點:釘住的快照集合、時間軸(調查中不含真相,結案後才有)、報告(結案前拒絕)。
- 錄 fixture:開旗標時結案另外寫契約 §9 的六個檔(注入前的 state 要在注入時先存起來;事件檔是這輪所有 SSE 事件加相對時間)。

## 契約在哪
- `../contracts/API.md` §3(snapshots、timeline、report 端點)§6(verification 形狀、結局清單)§7(timeline、report 物件)§9(fixture 六個檔)
- `../contracts/cards.yaml`(verify 的 settle 上限與謂詞、truth);schema:`report` `timeline`;fixture:`catalog_pool_leak/{report,timeline}.json`、`snapshots.jsonl`

## 怎麼算做完
- 假 stack + 真模型,catalog 卡注入、批准後 90 秒內:phase recovered、兩個窗口 passed、卡片 id 填好;報告端點有差值、早期跡象、真相;時間軸有系統層與 AI 層;調查中的報告端點是 409、時間軸沒有真相欄位。`check-live.ts <control> <事故 id>` 含報告與時間軸全綠。
- payment 卡一輪:recovered_mitigated,殘留裡有主供應商。租約給 910 秒、偵測後不批准:到期後 expired_before_approval、假店面回 v1。
- 開錄影旗標跑一輪,錄出的六個檔暫時放進 contracts 的 fixture 目錄跑 `bun ../contracts/check.ts` 要過,跑完刪掉。

## 測試
只寫契約邊界與主線:卡片謂詞的過與不過(含單調下降遇雜訊的例子);窗口機對固定快照序列的三種結局;租約在 awaiting_approval 到期的結局與安全還原標記;報告的差值算術與早期跡象挑選;錄出的六個檔過 schema。

## 不做什麼
對話模式、Grafana、環形落地、前端嵌入。

## 接線點
接 4(完整,第三個大檢查點):全 compose(店面 02 到 07、監控、你的 control、真模型)加前端 02 到 04,從注入到 recovered 走完,過關是 recovered 加 `check-live.ts` 全過。接 5:前端 05、06 的報告頁對你的三個端點拖拉回放、三層時間軸、早期跡象;再開錄影旗標錄一輪真的,交給協調者換掉 POC 的錄影。
