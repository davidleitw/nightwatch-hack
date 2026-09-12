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
- Chrome CDP 真實 DOM 啟用/解除，以及 desktop/mobile 截圖已完成；visual review 確認 desktop 三卡完整、390px mobile 單欄無溢出，`main.py`/`demo_faults.py` workspace 與 container hash 一致，前端部署 asset 為本次 production build。
- 未宣稱三張卡都完成自動到期驗證；`database_write_lock` 的 60 秒自動到期尚未單獨驗證。主機直接 `npm run build` 因 `node_modules` 缺少 `vite` 以 exit 127 結束，但 Docker production build 已成功。

## Microservices split — 2026-09-12（驗證進行中）

- `compose.yaml` 現在定義五個 containers：`frontend`、`backend` gateway、`catalog`、`cart`、`order`；公開 port 仍為 frontend `8080` 與 gateway `8000`，三個業務服務只使用內部 `8000`。
- 商品、匿名 cart、訂單各自使用 private SQLite volumes；gateway 只有 logs volume。舊 `shop-data` 以 `/legacy:ro` 保留給三個業務服務做一次性表級 migration，`make up` 會先停止舊 `frontend/backend` entrypoint 且不刪資料。
- 前端購物袋改用 server cart ID，首次載入會把舊 `daily-cart` 逐項匯入，成功後才清除舊 key；checkout 改走 `/api/carts/{cart_id}/checkout` 並帶 `Idempotency-Key`。六個 seed 商品新增按名稱映射的 generated product assets，自訂商品保留 icon fallback。
- 文件同步更新服務拓撲、跨 SQLite checkout 的 prepare/complete/abort 恢復語意、service failure 行為、logs 指令與 volumes。

## Demo fault control correction — 2026-09-12（待 backend manager 重建驗證）

- 故障卡不再自動到期；`checkout_exception`、`database_write_lock`、`checkout_delay` 會持續啟用，只有 `DELETE /api/demo-faults` 手動解除。
- fault response 維持既有欄位 shape，但 `lease_seconds`、active 的 `expires_at`、`remaining_seconds` 固定為 `null`；checkout delay 仍為每次 request 10 秒，頁面不再顯示倒數或 60 秒文案。

### 本輪尚未驗證

- 五服務 Docker production build、container health、既有 10 tests、smoke、Chrome checkout、migration 實際資料比對、service stop/recovery、idempotency retry 與新手動解除 fault shape 尚未完成；本輪不先宣稱通過。
- 已執行 `docker-compose -p nightwatch-shop-web -f shop-web/compose.yaml config`，設定解析成功。

## 2026-09-12｜微服務 production 驗證結果

- `make up COMPOSE=docker-compose` 已完成五個 production containers 的 build/recreate；`frontend`、`backend`、`catalog`、`cart`、`order` 的 Compose health 狀態均為 `Up (healthy)`。公開 host ports 仍為 8080 與 8000。
- 遷移前 legacy checkpoint 為 products 6、carts 0、cart_items 0、orders 34；首次五服務啟動後相同。後續測試/HTTP 操作建立資料，最後一次 volume inspection 為 products 6、carts 1、cart_items 1、orders 43。
- `make test COMPOSE=docker-compose` 實際跑完既有 10 cases，10/10 OK（68.038 秒）；`make smoke COMPOSE=docker-compose` 的首頁與 `/api/health` 通過。`uv lock --check` 通過，uv export 與 `backend/requirements.txt` 的套件版本一致。
- production frontend asset 已由首頁取到；六張 generated product image 逐張由 Nginx 回 HTTP 200。host 直接 npm build 未作為依據，因本機 node_modules 缺少 vite。
- 故障卡、節點 stop/restart、checkout idempotency retry 與瀏覽器互動由獨立驗證流程補測，本輪文件不把它們標為通過。先前 service-stop 真 HTTP 檢查觀察到 catalog 停止時 cart GET 回 500，並看到 catalog/cart log volume 沒有 `app.log`；若最終 backend snapshot 未修正，這兩項是交付前 blocker。

## 2026-09-12｜接手修正與最終驗證

以下結果取代上面 service split 的待驗證與 blocker 狀態。

- cart GET 遇到 catalog 中斷改回明確的 503，避免誤刪購物車內容。
- cart 保存 abort-before-prepare 的取消紀錄，避免背景恢復反覆 404，也防止遲到的 prepare 鎖住購物車。
- 前端保存原始 checkout payload；網路中斷與 HTTP 5xx 後鎖定修改，以相同 `Idempotency-Key` 和 body 重試，修正停用欄位被 FormData 遺漏而無法重試的問題。
- catalog/cart 沿用既有應用程式 logger，新增啟停、HTTP status/duration、例外 traceback 與私有 `/logs/app.log`。
- `make up` 若無法停止舊入口即停止，不忽略錯誤後繼續遷移。

本輪已完成五服務 production build 與重建，`frontend`、`backend`、`catalog`、`cart`、`order` 全部 healthy。`make test COMPOSE=docker-compose` 的既有 10 項測試全數通過（23.926 秒），`make smoke COMPOSE=docker-compose` 的首頁與 Nginx `/api/health` 通過。

真實 HTTP 驗證確認三張故障卡各自超過 65 秒仍 active，DELETE 後恢復；資料庫鎖約 10.10 秒後回 500，失敗未新增訂單。catalog/cart/order 停止與恢復、503 錯誤、商品與購物車保留，以及同 `Idempotency-Key` 重試已實測。以真實 cart SQLite 寫入鎖造成「訂單已提交但清空購物車失敗」後，同 key 重試與 order 重啟恢復均只保留一張訂單並清空購物車。abort 早於 prepare 與重複 abort 也已驗證。

Chrome 實際操作已確認六張商品圖載入、舊 `daily-cart` 只匯入一次、重新整理保留 server cart、瀏覽器斷線與 HTTP 500 後以原 key/body 重試成功、事件卡啟用與手動解除，以及 1440px/390px 事件頁沒有水平溢出。所有請求使用真實服務，未以 fake fetch 或 stub 代替。

實際比對 legacy volume 的 6 件商品與 34 張訂單，ID、內容與時間均保留（訂單 payload 在新庫由 TEXT 轉成 BLOB，解碼後內容一致）；舊 carts/cart_items 各為 0，因此沒有真實非空 legacy cart 可比對。四個後端服務的 `/logs/app.log` 都有 HTTP 紀錄。驗證商品與購物車已清理，驗證訂單保留，因 API 沒有刪除訂單功能。

未執行破壞性的 `make clean CONFIRM=yes`、跨主機部署、同服務多副本或實際金流／出貨；本輪也未等待跨 UTC 午夜驗證 log 輪替。故障狀態仍在 order 記憶體內，order process 重啟會清除；取消的是原本的 60 秒自動到期。
