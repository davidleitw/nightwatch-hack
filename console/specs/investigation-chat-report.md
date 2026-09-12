# 調查對話、獨立報告與歷史

## 目的與範圍
把右側目前調查改為對話紀錄，置頂一排 icon 與用量。報告以獨立卡片連結開啟獨立頁面，歷史可回看對話及報告。保留拓樸、Monitor、建立調查冪等性、SSE 重連與錄影模式。不新增套件，不新增對話輸入框。

假設：「thing」指 thinking／推理摘要；只呈現後端明確提供的公開摘要，不從工具參數推測模型思考。

## 已確認現況
依 console/src/investigation-data.js 與 investigations.js：
- SSE：GET /api/investigations/stream?after=<cursor>；外層 state、graph、investigation、ping。Monitor 另用 /events。
- investigation envelope：investigation_id、seq、cursor、type、at、payload。seq 排序及去重；cursor 用於重連，REST 不推進 SSE cursor。
- 已支援 investigation.started、tool.started、observation.recorded、tool.failed、report.submitted、investigation.finished。
- 工具以 investigation_id + payload.call_id 配對；report.submitted 要求 tool=submit_report 與 report 物件。
- 報告優先 report.investigation_report，兼容 report.agent_report；history/detail/context 已有流程。
- contracts/schemas/sse-line.schema.json 為舊串流，contracts/ 缺目前 investigation 契約；新增事件須先確認後端與授權範圍。

## 畫面與互動
1. 右側頂部：Agent 圖示、狀態、歷史入口；置頂一排 total token、cache 命中率、模型請求、工具呼叫。缺值顯示 —。
2. 對話時間線：觸發原因、Agent output 文字氣泡、可折疊 thinking 摘要；工具卡含名稱、狀態、時間、參數與結果，失敗明確呈現。Monitor 保留篩選與節點定位。
3. 調查依 seq 排序，保留捲動、工具展開與焦點；在底部才追蹤新事件，向上閱讀時提供跳到最新。
4. report.submitted 產生獨立報告卡，可先看提交結果，結案後讀保存報告；一般文字或結案錯誤不能冒充成功報告。
5. #investigations 為歷史清單，保留搜尋、篩選、分頁。#investigations/<id> 為獨立報告；#investigations/<id>/chat 為保存對話，互相切換。舊連結有效。
6. 結束後保留最近一筆對話與報告卡，直到下一筆 active 出現。服務健康只由 graph 更新。
7. SVG icon 內嵌，裝飾 aria-hidden，操作附文字；窄螢幕不溢出，尊重減少動態效果設定。

## 後端介面（本次繼續完成 control 與 console）
- 沿用既有 envelope，公開 thinking 摘要與 agent output 同時保存與回放；新增事件需兼容既有客戶端。
- 保持 submit_report 名稱。成功結案必須有實際驗證、保存的工具結果；模型未呼叫則要求補交，額度不足／工具錯誤明確失敗，不製造報告。
- 不揭露憑證、隱藏模型推理；前端內容當文字轉義。

## 驗收
- production build；以真實本機 control 驗證 state、history、detail、events、SSE、保存報告。
- 瀏覽器點擊歷史、報告卡、返回對話、搜尋、篩選、工具展開、節點定位、Usage、錄影入口；桌機／手機寬度。
- 重整不建立調查、事件不重複、失敗不冒充成功、缺用量不顯示 0。
- subagent 背景 review，主 agent 繼續驗證修復。
- 保留本機預覽，使用者說可以後才送 MR。未執行項目列入交付報告。

## 新增事件與向下相容
- 保存事件 type：agent.output、agent.thinking_summary、agent.reasoning_status、agent.usage。沿用 investigation_id、seq、cursor、at、payload。
- output／thinking_summary 的 payload 為 {message_id, text}；message_id 依模型回應及段落穩定生成。usage 的 payload 為 {usage}，沿用既有累計用量欄位。
- SSE 新訊息使用獨立外層 event: agent，舊前端沒有 listener 會略過；既有六種仍用 event: investigation。二者共用 envelope.cursor，斷線可回放；保留只有 investigation frame 帶 SSE id 的舊行為。
- GET /api/investigations/{id}/events?include_messages=1 包含訊息；預設保留舊六種事件。新前端遇到舊後端拒絕該 query（400）時改回既有讀取路徑，並提示文字回放不可用。
- 不改 schema_version 或既有 payload，不編造歷史文字。事件於每次模型回覆完成後發送，不聲稱逐 token 串流。
- thinking 僅採 OpenAI Responses 公開 summary 文字；不傳 signature、encrypted_content、raw_content。來源：[OpenAI Reasoning models](https://developers.openai.com/api/docs/guides/reasoning)。若 API 回傳 reasoning item 但沒有可讀摘要，發送 agent.reasoning_status，payload 為 {message_id, status: "summary_unavailable"}；畫面只顯示推理已發生與摘要不可用，不推測內容。沒有 reasoning item 時不產生推理訊息。
- graph 模式只接受 submit_report 的結構化輸出；保留框架重試與預算，結案前再次確認有成功 report.submitted。

## 本次驗收結果（2026-09-12）
- production build 13 個檔案完成；JavaScript／Python 語法檢查、git diff --check 通過。
- 既有 control 測試 23 項通過；模型替身測試不當作真模型驗證。
- 真模型 gpt-6-astra 對真實 Guard Room API 完成 inv-bc956373d46d4956be27254858fb1215：3 段 Agent 文字、3 次取證、1 次 submit_report；結果 completed/unresolved，3 項發現與 3 筆證據，保存摘要與工具提交相同。驗證用額度為 4 次工具／180 秒，預覽現已恢復既有預設額度。
- 新事件回放 21 筆；舊介面回放 10 筆。單筆分頁包含 11 個過濾空頁仍不漏事件；從 cursor 63 恢復精確得到其後 16 筆。
- 同筆舊資料在拓樸與 Usage 都為 111,112 tokens／90.01% cache；新資料兩頁都是 14,682／66.52%。
- 真實 Chromium 點擊歷史、搜尋、報告卡、對話切換、工具展開、上下文讀取、Agent 篩選、拓樸搜尋、Usage、錄影模式。390px 寬度對話與報告均無水平溢出，沒有 JavaScript 例外。
- 在瀏覽器刻意阻斷 detail 請求（其餘 API 保持真實）時，仍可由已收到的提交事件開啟 3 項報告發現，且錯誤保持可見；恢復連線後補齊證據。
- 後續真模型調查 inv-d9bab5f74b9344b0a6352a9e285456ae 回傳 429 字元公開摘要，已驗證展開、歷史與 SSE 回放；22 筆完整事件、10 筆舊介面事件。成功提交 3 項發現，completed/unresolved；一次取證失敗也保留可見。兩頁用量同為 23,730 tokens／70.72%。沒有逐 token streaming。
- 首輪 subagent review 完成，指出的報告 fallback 與焦點問題已修並在瀏覽器驗證；第二輪因帳號用量限制未能完成。
- MR 已整合 master 180c7a2（含 #37 用量刷新與共用欄位驗證），保留既有快照時間軸、節點位置與主要量測呈現；Chromium 已實際切歷史快照、回即時，右側對話仍在。390px 對話／報告無溢出，無 JavaScript 例外。
- API 僅回傳無文字 reasoning item 時的狀態事件已接線；最新真模型此次有摘要，未觸發該分支的完整端到端驗證。
- 依使用者最新指示停止額外 review，完成驗證後開 MR，不合併。4180 production 前端及 8010 隔離調查後端保留供使用者查看。
