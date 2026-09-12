## 做了什麼

- 新增 `console/nightwatch-presentation.html`，以 8 頁介紹 NightWatch 的問題、架構、調查工具、工作台、紀錄保存與 Demo 路徑。
- 單一 HTML 內嵌 CSS、JavaScript、SVG 與 Google Fonts Inter 英數字型，提供 16:9 縮放、左右鍵與按鈕翻頁、頁碼、淡入轉場及全螢幕。
- 假設：受眾為 Hackathon 評審與技術團隊、演講者為 NightWatch 團隊、深色綠系設計。中文使用本機 Noto Sans TC 或系統字型。
- 內容依專案說明、console 前端與最新 investigation 介面文件整理；架構與工作台圖明確標示為示意。新後端工具／匯出能力與待接線功能分開描述。

## 依據什麼驗證的

- 建置命令 `python3 console/.codex/build-nightwatch-presentation.py` 產出 77578 bytes、8 slides、fonts embedded；建置輔助檔不納入本 PR，HTML 可直接開啟，無需建置。
- 製作時以 Chrome 直接開啟 HTML，逐頁檢查全部 8 頁；修正第 6 頁文字對比後重新檢視。實際操作左右鍵、上下頁按鈕、End、末頁邊界與全螢幕。
- 提交前以 Python 讀取成品：77577 bytes、8 頁、2 個內嵌字型、沒有外部 CSS URL；credential-like pattern 檢查未發現符合的字串。
- HTMLParser 檢查外部資源標籤為空；`git diff --check` 無輸出。

## 沒有驗證的

- 本 PR 是簡報，不重跑真實 Monitor／Guard Room／Watcher、模型調查、自動觸發、報告匯出或修復。工作台示意與 Demo 腳本不代表端到端驗證。
- 未驗證其他瀏覽器、手機、列印、螢幕閱讀器或實際斷網環境；靜態檢查確認成品不載入外部資源。
- 新結構化報告的完整前端呈現仍待接線；調查結束不表示服務恢復。推送階段沒有重跑前一輪已完成的瀏覽器操作。

## 契約疑問

- `contracts/API.md` 仍是舊事故流程，而最新 `control/INVESTIGATION-FRONTEND.md` 與 `control/AGENT-REPORT-API.md` 使用 investigation 介面。正式契約是否會同步？本 PR 依最新介面文件介紹，未修改契約。
