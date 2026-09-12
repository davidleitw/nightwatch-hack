## 做了什麼

- `README.md` 改為英文首頁；標題、導覽、正文、表格與圖片替代文字皆使用英文，頂端保留 `繁體中文` 語言連結。
- 新增根目錄 `README.zh-TW.md`，保留完整繁體中文目標、能力、架構、啟動、對接與未來工作；頂端可切回 English，兩份文件共用既有進版圖。
- 從最新 `origin/master` 的 `b03d3b1` 建立獨立 worktree `/private/tmp/nightwatch-english-readme`，分支 `docs/english-readme-default`。原 PR #28 已合併，本次另開後續 PR，保留最新主線的演練故障解除說明。
- 假設：繁體中文版放在根目錄 `README.zh-TW.md`，讓兩份文件共用相對路徑，方便維護。

## 依據什麼驗證的

- `gh pr view 28 --json state,headRefName,baseRefName,url` 確認原 PR 已合併；`git log -3 --oneline` 確認新 worktree 以最新主線為基底。
- `python3 - <<'PY'` 檢查兩份 README：44 個本機檔案／標題參照均存在；英文首頁除語言連結外沒有中文；8 段 shell 範例均通過 `bash -n`，兩個語言版本的可執行指令完全一致。
- 對兩份文件執行 `gh api markdown --input ...`，GitHub GFM 轉換成功；解析 HTML 確認兩邊語言切換連結、共用進版圖與各 4 張表格均保留。
- `python3 - <<'PY'` 啟動隔離本機 HTTP server，兩份 README 及進版圖皆 HTTP 200；最後輸出 `Verification HTTP server stopped`，已關閉驗證 server。
- `git diff --check`：無輸出。

## 沒有驗證的

- 未執行瀏覽器版面／點擊驗證；頁內導覽核對標題名稱，GitHub Markdown API 未提供實際錨點 ID。
- 本次僅調整文件語言與組織，沒有重新啟動應用服務或執行模型調查；shell 範例只驗證語法及雙語一致性，未實際執行。

## 契約疑問

無。
