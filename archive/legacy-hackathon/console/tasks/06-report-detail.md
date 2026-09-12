# console 06:報告頁 II:早期跡象、那一刻 agent 在做什麼、報告本文、工具明細

## 為什麼要有這段
報告頁要能回答「AI 比真相晚多少、比偵測早多少」和「那一秒它在做什麼」,而且要看得出哪些字是模型寫的、哪些是 Go 算的。這段補上這些,並讓報告能印成一頁。

## 目標
- 早期跡象特寫固定一張卡:那一格的節點、軸、值,旁邊三個差值直接讀報告物件,前端不自己算,缺值顯示「—」;一句人話把三個數字串起來。
- 右下「那一刻 agent 在做什麼」:沿用 04 的活動流,自動捲到游標秒數對應的事件,之後的淡出;游標在偵測前時說明 agent 尚未介入。
- 報告本文可展開:根因、信心、AI 時間軸逐條、五項稽核各自結果與理由、三段耗時;模型寫的字有標籤。
- 工具明細:這一輪叫過的工具一覽(名稱、對象、耗時、證據編號),點一筆跳到活動流那一列。
- 可列印:一頁 A4 放得下標頭、早期跡象、時間軸、報告本文。

## 契約在哪
- `../contracts/API.md` §4 SSE cursor(live 拿整輪事件的方式)、§6 read model 與 journal、§7 report 的 early_sign、comparison、audit、ai_timeline、§8 工具結果
- `../contracts/schemas/{report,incident-commit}.schema.json`、`../contracts/fixtures/catalog_pool_leak/{report.json,events.jsonl}`

## 怎麼算做完
- `?fixture=catalog_pool_leak#view=report&incident=<錄影裡的 id>`:三個差值跟 `report.json` 一致;游標拖到偵測前右下寫 agent 尚未介入,拖到排查中活動流捲到對應的工具卡、之後淡出;報告本文與工具明細齊全。
- 把 `report.json` 複製一份把差值改成 null,顯示「—」不報錯。
- 對假 control 播到結案後,live 的右下活動流也有整輪內容。
- 列印預覽一頁放得下;截圖存 `.shots/06-report-detail.png`、`.shots/06-report-print.png`。

## 測試
只寫契約邊界與主線:差值 → 句子(含 null);游標 → 活動流定位;工具明細的抽取。

## 不做什麼
嵌入、接真 control、投影字級、排練開關。

## 接線點
05 + 這段 = 接 5;錄下的新 fixture 換掉舊錄影後,離線回放的報告頁要一樣能開。live 拿整輪事件的做法可不可行,寫在回報裡給協調者決定要不要加端點。
