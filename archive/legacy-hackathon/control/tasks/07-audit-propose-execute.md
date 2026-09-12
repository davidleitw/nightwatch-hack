# control 07:稽核、動作解析、提案、批准、執行

## 為什麼要有這段
模型說的話不能直接動店面。這段由 Go 用證據與量測稽核模型的結論、把根因對到 manifest 裡允許的動作、做成提案給人批准,批准後才照步驟執行。

## 目標
- 五項稽核照契約定義,每項有狀態與理由:證據引用要存在;直接根因要是呼叫圖上的葉子(外部節點、佇列、磁碟先映射到它們的擁有者);根因機制要對得到一個允許的動作;時間軸的起點與順序要跟量測一致,順序不對時直接根因也算不過。稽核不過就以 audit_rejected 結案。
- 稽核過就建提案(動作、目標、中文描述、範圍、引用的證據、怎麼驗證、是不是只繞過),事故進入 awaiting_approval。
- 批准端點:階段不對、提案不是目前這份、重複請求,各自照契約回應;通過就記錄批准、進入 executing。
- 執行照 manifest 的步驟(回滾送完整出廠表、切一顆旋鈕先讀再改、打一個 URL),每步結果記在 read model;任一步失敗以 action_failed 結案。成功後對應的故障實例變 restored;只繞過的動作實例維持 active 但標 mitigated。
- 動作成功後曲線引擎要停止推那張卡的旋鈕;只繞過的卡繼續推(主供應商還是壞的,刻意的)。執行完進入 verifying,驗證窗是下一段,這段先停住不結案。
- 中止端點:取消進行中的排查或執行、全部還原、以 aborted_by_operator 結案;已結案的中止要拒絕。

## 契約在哪
- `../contracts/API.md` §3(approve、abort 端點與錯誤碼)§6(proposal、approval、execution 的形狀與階段轉移)§7(五項稽核的定義)
- `../contracts/SHOP-INTERNAL.md` §2(出廠表)§4(三種步驟)
- `../contracts/manifest.yaml`(mechanism_families、actions)、`cards.yaml`(truth);fixture 的 events.jsonl 找提案、批准、執行事件看 payload

## 怎麼算做完
- 假 stack + 真模型,注入 catalog 卡:偵測後 20 秒上下進入 awaiting_approval,五項稽核 passed,提案是回滾 catalog;錯的提案 id 被 409;批准 202、重送同 request_id 回同一個操作、再送新的被 409;假 catalog 回 v1 且 10 秒內不會再被推成 v2;實例變 restored;事故停在 verifying。
- payment 卡走一輪:提案是切換供應商、只繞過;批准後假 payment 的 active 變 secondary,錯誤率還在漲。
- 中止:在 awaiting_approval 按中止,202、結局 aborted_by_operator、假店面回 v1。`check-live.ts <control> <事故 id>` 通過。

## 測試
只寫契約邊界與主線:五項稽核各一組通過與駁回(含外部節點映射到 payment、佇列映射到 fulfillment、順序不過連帶直接根因不過);動作解析對四種機制字串的結果;批准的三種拒絕與冪等;執行對假店面送的是完整出廠表、切旋鈕只改一顆;動作成功後不再推曲線、只繞過的繼續推。

## 不做什麼
驗證窗、其餘結局、報告與時間軸端點、錄 fixture、對話模式。

## 接線點
接 4 的後半:前端 04 的提案卡與批准、中止按鈕對你的 control:舞台頁在 awaiting_approval 出現提案卡,批准後活動流出現批准、開始、完成;按兩次不重複執行;在 executing 再按是 409 且畫面顯示契約的中文訊息。全 compose 版等下一段的驗證窗好了一起做,不然事故卡在 verifying,demo 看起來像壞了。
