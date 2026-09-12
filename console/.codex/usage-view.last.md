## 做了什麼

- 在即時工作區右上角新增 Usage 入口，開啟獨立 `?source=live#usage` view，顯示目前或最近一次調查的總 token、Prompt cache 命中率、模型請求、工具呼叫及 token 明細。可返回拓樸或查看調查報告。
- 趨勢區塊分成 token 與 cache rate 兩張卡；目前沒有時間序列，明確顯示「尚無趨勢資料」。假設依「style 相關即可」，本次不新增趨勢 API 或捏造折線。
- 假設 `cache_read_tokens` 包含在 `input_tokens` 中；命中率按兩者比值呈現。新 view 讀取實际後端的 `cache_read_tokens`、`cache_write_tokens`、`requests`、`tool_calls`，相容舊 `cached_tokens`、`calls`；原面板與原統計程式維持原樣。
- 先完成並依指定範圍更新 `console/specs/agent-usage-style.md`，修改 `console/src/index.html`、`console/src/investigations.js`、`console/src/app.css`，本報告為 `console/.codex/usage-view.last.md`。新樣式限定於 Usage view 與新入口。未新增套件、測試或 reviewer。

## 依據什麼驗證的

- `python3 console/build.py`：成功產生 13 個檔案，共 1,642,177 bytes；不需網路與第三方依賴。
- `node --check console/src/investigations.js`、`git diff --check`：通過，無錯誤輸出。
- Python `urllib` 請求使用者指定的 `4176`：`index.html`、`investigations.js`、`app.css` 均 HTTP 200，內容與本次 production build 完全相同。
- 實際 GET `4176/api/investigations/state` 與最近一次 detail：HTTP 200，沒有進行中調查；該次已完成調查回傳輸入 108,101、輸出 3,011、快取讀取 97,300、快取寫入 9,037 tokens、模型請求 14 次、工具呼叫 13 次。依公式算出總量 111,112、cache rate 90.01%。
- Python 經 `gh api` 與 master 比對：`renderUsage`、`renderCurrent`、`renderDetail`、`renderGraph`、`renderHistory`、`activityHTML`、`reportHTML` 七個原面板函式逐字相同；原 CSS 完整保留。
- `git status --porcelain` 確認本次程式修改限於 console。此次在 `4279` 啟動的驗證服務已透過執行 session 的 Ctrl+C 關閉，輸出 `NightWatch server stopped.`。

## 沒有驗證的

- Chrome headless 啟動兩次均以 134／SIGABRT 結束，Launch Services 也未能啟動 Chrome，因此未完成桌面／窄螢幕肉眼檢查、實際點擊與前進返回操作。`4176` 已供應新版，可直接開啟 `http://127.0.0.1:4176/?source=live#usage` 檢視。
- 已核對 API 真實數字與公式，尚未以瀏覽器核對數字渲染與新 view 的互動。未建立新調查、未呼叫模型；進行中切換與無資料、異常資料狀態尚未實際操作。
- 沒有用量時間序列，未驗證真實趨勢折線。快取欄位統計語意依上述假設，不代表已獲契約確認。

## 契約疑問

- `contracts/` 尚無 usage 定義：請確認 `cache_read_tokens` 是否包含於 `input_tokens`，`requests` 與舊 `calls` 的口徑是否一致，以及未回報與未來時間序列的格式；本次只在新 view 讀取原樣欄位，不修改後端。
