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

## 商品、購物車與 backend 交付 — 2026-09-12

- 新增商品 CRUD、匿名購物車 CRUD 與購物車 checkout，使用 SQLite 保存資料與訂單快照；完整 endpoint、schema 與限制見 [Backend CHANGELOG](../backend/CHANGELOG.md)。
- 以 `pyproject.toml`、`uv.lock` 與匯出的 `requirements.txt` 固定 backend 依賴與 Python 3.12 執行環境。
- 新增 `INFO` 等級 application logging，輸出 stdout 與 `/logs/app.log`，使用 UTC 每日輪替並保留 7 份備份。

## 故障演練頁 — 2026-09-12

- 前端新增 `/#/events` 事件頁，維持 React + Vite 且不新增 router 或第三方依賴；商店頁提供返回事件頁的入口，事件頁可返回商店。
- 事件頁固定呈現 `checkout_exception`、`database_write_lock`、`checkout_delay` 三張卡，顯示影響範圍、server 狀態、剩餘秒數與啟用/解除操作；同時間只允許一種故障，操作中會防止重送。
- 事件頁使用 `GET/POST/DELETE /api/demo-faults`，POST body 為 `{ "fault_id": "..." }`；成功狀態以 server response 為準，約每 2 秒輪詢，離開事件頁時停止輪詢。
- 三種故障的操作限制為 60 秒 server lease、結帳延遲 10 秒、SQLite 寫入鎖最多等待 10 秒；事件頁不自動建立訂單。

### 本次驗證

- `make up COMPOSE=docker-compose` 成功完成兩個 production images 的建置與啟動；`make test COMPOSE=docker-compose` 的既有 10 tests 全部通過，`make smoke` 通過首頁與 health。
- 真 HTTP 驗證包含 `/api/demo-faults` cards、busy 409、兩個 checkout 入口的 `checkout_exception` rollback、`database_write_lock` 約 10.18 秒後 500、DELETE 即時控制與寫入恢復，以及 `checkout_delay` 手動解除約 0.83 秒喚醒。
- 驗證剩餘約 8.64 秒時 `checkout_delay` 由 TTL 自動喚醒；backend restart 後 fault inactive 且寫入恢復，log 含 traceback、500 status 與 fault ID。
- Chrome CDP 真實 DOM 啟用/解除，以及 desktop/mobile 截圖已完成；最終截圖 hash/visual review 尚待核對。
- 未宣稱三張卡都完成自動到期驗證；`database_write_lock` 的 60 秒自動到期尚未單獨驗證。主機直接 `npm run build` 因 `node_modules` 缺少 `vite` 以 exit 127 結束，但 Docker production build 已成功。
