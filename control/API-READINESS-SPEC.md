# 前端 API 缺口與本次實作

日期：2026-09-12。依使用者要求直接實作，不另做 review。
範圍僅 control/。整合分支 feat/backend-api-readiness-mr 以最新 master 為基礎，
只包含本次 API 修改；原 worktree 的既有 agent 修改由其擁有者另行提交。

整合更新：master 的 #19 已接上原生 investigation 工作台、建立調查 POST 與報告瀏覽。
以下盤點保留最初發現；舊 incident/state 接線現在主要供相容使用。前端接線依
console/GUARDROOM-INTEGRATION.md，尚未在本輪做瀏覽器端到端驗證。

## 盤點依據

讀取 console/src/data.js、app.js、connect.js 的實際呼叫（使用者授權盤點前端）、
contracts/API.md 與 schema、control/INVESTIGATION-FRONTEND.md，以及 server 實作。

| 前端需求 | 修改前現況 | 本次處理 |
| --- | --- | --- |
| 拓樸初始 state、節點 layout | /api/graph 已接 monitor；/api/state、capabilities 關閉 mock 就 503 | 直接投影同程序 GraphStore，確保節點與 layout 相符 |
| 即時 graph、日誌、重新連線 | /events 真實模式只有 log/ping，無 state/graph | 加入 state、graph 與調查 journal 的相容投影；保留 log、心跳與 cursor |
| 事故列表、詳情、Agent 用量 | console 使用 /api/incidents；持久化調查在 /api/investigations | 舊讀取端點投影實際 sessions、evidence、usage，最新進行中調查出現在 state.incident；完成後移入歷史 |
| 調查報告 | detail 已有 report，沒有單獨 report 入口 | GET /api/investigations/{id}/report 原樣回已保存報告；執行中 409，未知 ID 404 |
| 調查當時看到的 graph | 工具 evidence 已保存，不應依賴 15 分鐘環形重讀 | GET /api/investigations/{id}/snapshots 回 get_graph 實際 evidence 的 evidence_id 與 snapshot；按證據順序，不補造未查詢的快照 |
| debug 日誌 | POST /api/logs 已持久化；GET /api/debug/logs 仍 503 | 從 GraphStore.recent 按發生時間新到舊投影既有 time/service/severity/body/trace_id，service 用 config node ID |
| readiness | 固定全部未接入 | logstore 依真實來源新鮮度；未探測模型、未實作基線/修復仍非 ready |
| 完整實驗報告 | 舊 /incidents/{id}/report 僅 mock：無實際注入 truth、稽核、基線偏離及修復窗口 | 503 清楚說明缺少資料，附既有調查報告路徑；不偽造根因、AI 起點或 recovered |
| 故障卡注入/還原、批准/換輪/操作查詢 | mock only；新 shop-web 與旧 contracts 的修復目標不同 | 保留明確 503，不做假成功；需服務擁有者提供契約與真實 actuator |
| history 五軸/基線、三層 timeline | 原始 graph snapshots 已有；舊 history 必須全數字與基線 | 不填零湊 schema；本次用原始保存 graph，舊 timeline 不冒充完整實驗資料 |
| Chat、告警 detector、歷史 trace 查詢 | 未接入、畫面沒有這些操作 | 列為未完成，不在本次啟用自動外連或模型執行 |
| 瀏覽器啟動調查 | 原盤點時缺 POST proxy；master #19 已提供工作台、POST 與原生報告瀏覽 | 已同步該主線提交；本次不另改 console，瀏覽器整合仍待驗證 |

## 假設與相容規則

- 本次「實驗報告」先交付已保存的調查摘要、證據、用量與觀測；這不表示已執行故障實驗。
- 舊事故讀模型展示調查：detected_at 使用 started_at，detection 明寫是調查啟動而非自動偵測；
  phase 為 investigating 或 unresolved，結案 outcome 只保留 budget_exhausted，其他一律 unresolved，
  因為沒有執行修復。原始 status/outcome 與報告保持在 investigations API。
- 每次程序启动的 live run 為獨立 ID；重連到舊 run 送新 state，歷史 sessions 跨重啟保留。
  incident revision 使用持久化全域 cursor；journal 按 cursor 排序，分批讀取，不整批重讀。
- legacy 事件映射：investigation.started、tool.started、observation.recorded 原名；
  tool.failed 以 observation.recorded 表示一次失敗觀察並保留原始 payload；
  investigation.finished → incident.completed（只表示調查結束）。原始事件在 investigations API 保留。
- GraphStore 的 config 沒有 layout，按 config 節點順序三欄排列；不修改 monitor 的量測或健康語意。
- baseline 不存在時 collecting/0/120；沒有本程序管理的故障實例時 instances=[]、generation=0，
  fault_clear 仍 waiting，不能據此宣稱外部服務無故障。model.available=false 表示尚未探測。
- 不增加套件，不修改契約，不建立測試；實際啟動 production uvicorn 與現有測試驗證。

## 驗收

1. 關閉 mock，實際 /api/state 與 capabilities 可讀，graph 與 /api/graph 同來源，通過現有 JSON schema 與前端 validator。
2. /events 有 state/graph/ping，寫入真實 monitor 事件後可收到 log、讀回 debug logs，重啟仍可讀。
3. 無模型金鑰時 POST 調查仍保存失敗報告，列表、詳情、report 可讀；重啟仍一致。
4. journal cursor 重播、格式錯誤、未知 ID、未完成報告正確處理，mock 仍可使用。
5. 既有 tests、離線 build；所有本次啟動程序關閉。未實際驗證模型與 shop 的部分明列報告。

## 契約疑問

- 舊 report.schema.json 必填 root_cause 與 ai_timeline.onset，且不接受 null；
  沒有確認根因的調查應如何表示？目前原生 investigation report 能表達，舊實驗 report 回 503。
- 新 Guard Room config 未定義 layout；先依順序三欄，是否由 config 擁有者提供正式 layout？
- 舊事故契約沒有手動調查的獨立 lifecycle；本次相容投影是否保留，或由前端全面切換 investigations？
