## 做了什麼

- 重寫根目錄 `README.md`：加入繁體中文與英文定位、目標、現有能力、架構、啟動方式、模型設定、接入方式、API 範例、未來工作與文件導覽。
- 新增 `docs/readme/nightwatch-hero.png`：內建 imagegen 生成的 2172 × 724 夜間瞭望塔插畫；原始 prompt 與版面參考保存在 `docs/readme/README.md`。圖片存於 repository，不依賴外部圖床。
- 新增 `docs/readme/integration-status.md`：整理既有盤點，區分 live、mock、未接通的故障操作、監測覆蓋與修復缺口；未將既有測試紀錄當成本次驗證。
- 假設：雙語共用啟動指令及 API 表格，中文與英文皆有完整目標、能力與限制說明；roadmap 表達建議順序，沒有承諾交付日期。
- 假設：這次明確要求根目錄 README、worktree 與 MR，適用於這項文件工作；因此使用獨立 worktree 與文件分支提交。
- 最終 worktree：`/private/tmp/nightwatch-bilingual-readme-pr`；分支：`docs/bilingual-readme-pr`。以 GitHub `master` 的 `2b4dfc6` 為基底。本機另有未推送的 `181944c` 盤點／清理，這次沒有納入它的 105 個檔案變更；改附自足的對接摘要，避免連到未推送的文件。

## 依據什麼驗證的

- 使用 `rg`、`cat`、`git show --stat`、`git diff --name-status` 閱讀 README、介面文件與既有 `INTEGRATION.md`，確認專案現況、文件基底及提交範圍；沒有讀取鄰接元件程式碼來推測介面。
- 查閱 Supabase 與 Hoppscotch 的 GitHub README，參考品牌圖、簡短定位及導覽層次；來源連結存於素材說明，沒有複製其圖像或產品宣稱。
- `sips -g pixelWidth -g pixelHeight docs/readme/nightwatch-hero.png`：2172 × 724；生成圖片已直接檢視，標題與副標題正確。
- `python3 - <<'PY'` 執行文件檢查：最終三份 Markdown 的 35 個本機檔案／標題參照均存在；4 段 shell 範例通過 `bash -n`。這只代表語法通過，沒有執行範例中的服務命令。
- `gh api markdown --input /private/tmp/nightwatch-README-final.json` 與 integration-status 對應命令：GitHub GFM 轉換成功；解析 HTML 確認主 README 有 4 張表格、1 張圖、16 個標題，對接摘要有 3 張表格、5 個標題。
- `python3 - <<'PY'` 啟動隔離本機 HTTP server，實際讀取 README、素材說明、對接摘要與 PNG，四項皆 HTTP 200；PNG 格式與尺寸正確。最後輸出 `Verification HTTP server stopped`，已關閉驗證 server。
- `git diff --check`：無輸出。

## 沒有驗證的

- 未重新建置／啟動商店、Guard Room 或 Console，未執行真模型調查、SSE 續傳、故障注入或修復流程；本次是文件及圖片變更，功能描述依據既有文件與盤點。
- Chrome headless 嘗試啟動本機預覽時 exit 134，沒有取得桌面／手機截圖；未確認真實 GitHub 網頁的最終視覺版面或實際點擊導覽。Markdown API 不輸出標題錨點 ID，導覽只核對對應標題名稱。
- README 的啟動與 curl 範例只核對介面及 shell 語法，沒有作為新環境完整安裝流程執行。

## 契約疑問

無。
