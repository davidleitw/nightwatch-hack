## 做了什麼

本次提交只包含 `console/schema-draft/` 的八個檔案，交接入口為 [README.md](README.md)。

| Schema | 用途 |
|---|---|
| [state.schema.json](state.schema.json) | 初始狀態、節點清單與事故進度 |
| [graph.schema.json](graph.schema.json) | 左側服務圖、健康與量測 |
| [event.schema.json](event.schema.json) | Agent 查詢、證據與判斷 |
| [log.schema.json](log.schema.json) | Monitor log 即時推到右側 |

提供四份 schema、兩份 JSON 範例、README 與本報告。**8–12 個是版面目標，schema 不寫死數量或服務名單。** 舊 spec 與 `.codex/` 歷次紀錄留在本機，不納入提交；後端程式與正式契約未改。

假設第一版 log 只做連線期間即時推播，不補歷史；同一輪版面保持穩定，名單變更時由完整 state 同步。

## 依據什麼驗證的

`python3` 查核四份 schema、兩份範例的 JSON 語法、遞迴引用、動態節點規則與 log 欄位；`git status --porcelain` 確認新增檔案均在 `console/`。

## 沒有驗證的

未跑完整 JSON Schema validator，未接真實 monitor／後端 SSE。

## 契約疑問

新增 SSE `log`、節點投影與事件 `refs` 必填規則是否同步納入正式契約？Monitor 到節點的對應、工具失敗的終止格式仍需後端確認。
