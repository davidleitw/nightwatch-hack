# Agent 工具、報告與完整紀錄 API

本輪範圍為 agent 本身與報告歸檔；沿用既有調查觸發方式。未執行任何驗證或 review。

## 模型的五個工具

Live Guard Room 使用 get_graph、list_graph_snapshots、get_node_detail、search_logs、submit_report。
前三個沿用既有來源；search_logs 讀 GET /api/debug/logs，參數與限制見 README。
帶 query selector 的 graph 來源只使用 get_graph 與 submit_report，避免混入其他 live 資料。

submit_report 是 PydanticAI 的 output tool，由 framework 結束模型 loop；
模型不呼叫 HTTP 來保存自己、不傳回整份 context，也不操作 SQLite。
它不消耗 20 次觀測查詢的次數，仍受模型請求、900 秒與 token 總預算限制。
格式或引用錯誤可重交一次；可交出證據不足的結構化報告。

## submit_report 參數

| 欄位 | 格式與用途 |
| --- | --- |
| summary_zh | 非空字串，整次調查摘要 |
| conclusion | supported 或 inconclusive；不表示修復或恢復 |
| findings | 陣列：summary_zh、node_ids、evidence_ids；每項 finding 至少引用一筆實際證據 |
| hypotheses | 陣列：cause_zh、node_ids、supporting_evidence_ids、counterevidence_ids、uncertainty_zh |
| limitations | 字串陣列；inconclusive 必須至少列一項限制 |
| next_steps | 字串陣列，下一步需要的資料或動作；並未執行這些動作 |

所有欄位必填，陣列可為空（上述必要條件除外），拒絕額外欄位。
節點必須在本次提供的 node 表裡，引用必須來自本次成功工具回傳的 evidence ID。
supported 必須有 finding；不硬性要求特定工具或 trace 才能交報告。
後端加 schema_version=nightwatch.investigation-report.v1，不由模型填寫。

## 保存流程

1. 既有 session manager 建立 ID、started_at、trigger 與 investigation.started。
2. Loop 保存 instructions、tools（含 submit_report）、opening、model 設定。
3. 每次 framework 迭代保存目前 messages 與 usage；模型回應與工具回傳保留原始 framework 格式。
4. 工具事件／證據逐筆落盤；submit_report 通過檢查時保存 report.submitted 事件。
5. Loop 結束後，同一交易寫入 terminal report、最後 context/evidence/usage 與 investigation.finished，釋放 active slot。

失敗、逾時、中斷也保存可讀摘要；沒有有效模型報告時 investigation_report 為 null。
中斷／崩潰保留已落盤的對話與事件，context.complete=false；不承諾保存被中斷的網路回覆。
模型不會得到金鑰或服務認證物件；API 匯出的是實際保存的模型可見內容，可能含應用日誌。

## 讀取報告

GET /api/investigations/{id}/report 回傳：

- investigation_id、outcome、summary_zh、started_at、closed_at。
- investigation_report：本輪的新結構化報告；没有模型報告為 null。
- agent_report：保留給舊錄影報告；新格式不塞入舊 root_cause/timeline 結構。
- evidence_ids、limitations。

執行中 409 investigation_active，未知 ID 404 not_found。
GET /api/investigations/{id} 的 report 仍為同一份內容，evidence 與 usage 仍在 detail 層。
舊資料沒有 investigation_report 欄位時，前端按 null 處理。
前端可先顯示 summary_zh，若 investigation_report 存在再渲染 findings/hypotheses 等區塊。
本輪沒有修改前端 rendering。

## 一次匯出整次調查

GET /api/investigations/{id}/export 回傳 application/json、Cache-Control: no-store。
只讀保存資料，不啟動模型或重新查詢 graph。未知 ID 回 404；執行中可以讀取部分匯出。

| 欄位 | 內容 |
| --- | --- |
| schema_version | nightwatch.investigation-export.v1 |
| exported_at | 本次匯出時間；不冒充觀測時間 |
| complete | 已結束且保存的 context 完整時為 true；不表示調查成功 |
| session | ID、request、trigger、開始／結束時間、status/outcome、context 完整度等 metadata |
| session_start | 原始 investigation.started 事件（含 at、seq、cursor、payload） |
| session_end | 原始 investigation.finished 事件，執行中為 null |
| report | 上述 terminal report，執行中為 null |
| context | {complete, context:{instructions,tools,opening,model,messages,usage}} |
| events | 全部已保存事件，按 seq 排序，不分頁、不截斷 |
| evidence | 模型實際收到的工具證據，保留既有截斷標記 |
| usage | 最新已保存的模型使用量；未完成的供應商請求可能尚無用量 |

Export 由 session store 在同一把讀寫鎖內組合，避免對話與事件讀到不同時點。
context.messages 是 framework 訊息，不是模型重寫的摘要；有既有 input/output/tool-result。
Export 包含生命週期資訊；單獨的 GET /context 不含 session_start/session_end。

## SSE 與前端

沿用 GET /api/investigations/stream，SSE event 名稱仍為 investigation，內層 type 新增 report.submitted。
payload 含 call_id、tool=submit_report 與報告 body，與該輸出工具的 tool.started 配對。
失敗重交走 tool.failed；report.submitted 尚不代表終態已歸檔。
收到 investigation.finished 後讀 report/export；active pointer 仍只依 state 更新。
現有前端可忽略新事件，繼續用 finished 與 detail 顯示摘要；完整報告版面需接 investigation_report。

## 執行入口與交接

CLI 與 HTTP 都使用相同工具與結案格式；CLI 有效的 inconclusive 報告 exit 0。
CLI 沿用 stdout/stderr，不自行寫 HTTP session 資料庫；HTTP manager 才提供上述持久化 API。
未改觸發方式、前端、依賴、contracts 或啟動腳本。合併後由服務擁有者載入新版並自行驗證。
本輪未執行 build、測試、CLI、真模型 API、瀏覽器或 review。
