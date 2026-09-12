## 做了什麼

- Agent 的 live Guard Room 模式提供 get_graph、list_graph_snapshots、get_node_detail、search_logs、submit_report。新增日誌工具接既有 GET /api/debug/logs，保留時間／筆數／截斷範圍，查詢錯誤明確回傳。
- Graph 調查以 submit_report 輸出結構化摘要、findings、hypotheses、limitations、next_steps；移除 graph 模式的舊 history／trace 必要條件，保留格式及證據／節點引用檢查。證據不足可提交 inconclusive；CLI 有有效 graph 報告時 exit 0。
- Loop 每輪保存 framework messages 與 usage，結案自動歸檔 session、prompt、工具、對話、事件與證據。失敗／中斷保留已保存內容及不完整標記。
- GET /api/investigations/{id}/report 新增 investigation_report，保留舊 agent_report 與 summary_zh。新增 GET /api/investigations/{id}/export，一次回傳 session_start/session_end、session、report、context、events、evidence、usage。
- 修改 control/nightwatch_agent/{graph,loop,prompts,__main__}.py、新增 report.py；修改 control/server/{investigation_api,investigation_store}.py；同步 README、AGENT-CONTEXT、INVESTIGATION-FRONTEND、server/README，新增 AGENT-REPORT-API.md。
- 假設：live graph URL 與日誌 API 共用 base path，graph 路徑結尾為 /graph；帶 query selector 的來源只掛 get_graph 與 submit_report。前端可先沿用摘要，完整區塊由前端讀取 investigation_report；本 MR 不修改前端或觸發方式。

## 依據什麼驗證的

無。依使用者明確指示，未執行任何驗證或 review。

## 沒有驗證的

全部程式行為均未執行驗證；未跑測試、build、語法檢查、CLI、curl、真模型 API 或瀏覽器。交由使用者驗證 local agent loop 與實際 API。CLI 沿用 stdout/stderr；HTTP manager 才保存可由 report/export 讀取的 session。合併與服務更新交給其他 agent／服務擁有者。

## 契約疑問

無。本輪依使用者核准的工具與報告 API 範圍實作。
