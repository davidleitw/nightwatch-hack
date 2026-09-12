# 變更紀錄

本文件位於 `shop-web/docs/`，記錄日日選物示範專案的可辨識變更。歷史版本曾使用 `shop-web-eample/docs/`，本次搬移保留該舊路徑脈絡。

## [Unreleased] — 2026-09-12

- 建立 `shop-web/` 專案，提供 React + Vite 簡易購物前端與 FastAPI 後端。
- 前端提供商品載入、分類篩選、關鍵字搜尋、購物袋與模擬結帳流程。
- 後端提供健康檢查、商品目錄與訂單 API，使用 SQLite 保存訂單。
- 以 Docker Compose 維持兩個 containers：`frontend` 與 `backend`；前端容器內的 Nginx 負責靜態頁面與 `/api/` proxy。
- 以 `shop-data` named volume 保存 `/data/shop.db`，讓 containers 重建後仍能保留訂單資料。
- 新增 Makefile，集中管理 Docker images 建置、重新建置、啟動、停止、重啟、log、狀態、設定檢查、API 測試、smoke check 與資料清理。
- 新增 `shop-web/README.md` 繁中操作文件；變更紀錄與討論紀錄當時依指定放在舊路徑 `shop-web-eample/docs/`，後續已搬至 `shop-web/docs/`。

## 驗證結果 — 2026-09-12

- `make up` 成功建置並啟動 `frontend`、`backend` 兩個 containers。
- `docker inspect` 確認 `frontend` 與 `backend` 均為 healthy。
- `make smoke` 通過首頁與經 Nginx 代理的 `/api/health` 檢查。
- 直接在 backend container 執行 `python -m unittest discover -s tests -v` 顯示 4 tests OK；`make test` 亦以 exit 0 結束，但本機舊 experimental Compose 沒有輸出測試明細。
- backend 重啟前後都確認有相同四筆測試訂單，且 proxy health check 維持 OK，表示訂單資料在重啟後仍可讀取。
- 前端 production build 成功。
- 未執行瀏覽器自動化互動測試。

## 整合紀錄 — 2026-09-12

- 使用者同意繼續 `shop-web/` 並忽略既有 `shop/`；原有 `shop/` 等遠端內容保留，文件先依需求放在 `shop-web-eample/docs/`，現行位置為 `shop-web/docs/`。
- main agent 從 `origin/master` 的 `e12599a` 建立 `feat/integrate-shop-web`，以 `--allow-unrelated-histories` 成功整合本機 `main`；雙方原本的獨立歷史均保留。
- 整合當時尚未 push；後續已推送 `master` `4808338`，遠端 `main` 已移除。

## 文件搬移紀錄 — 2026-09-12

- 在 `chore/shop-web-docs` 分支將 `CHANGELOG.md` 與 `DISCUSSION.md` 從歷史路徑 `shop-web-eample/docs/` 搬至現行路徑 `shop-web/docs/`，並移除空的舊目錄。
- README 與文件內的現行連結已改指向 `shop-web/docs/`；舊路徑只在歷史紀錄中保留。
- 後續功能工作從最新 `master` 開功能分支，PR 目標為 `master`，經 reviewer review 後才合併。
