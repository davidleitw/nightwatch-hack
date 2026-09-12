# 討論紀錄

本文件現位於 `shop-web/docs/`，保留需求、取捨與交付脈絡；歷史版本曾位於 `shop-web-eample/docs/`。

## 2026-09-12｜需求與工作範圍

- 使用者要求製作一個簡易購物前端，前端 framework 可由實作方選擇。
- 後端指定使用 FastAPI。
- Compose 部署限定為兩個 containers；本實作將服務分為 `frontend` 與 `backend`，Nginx 放在前端 container 內處理靜態頁面與 API proxy。
- 使用者要求以 Makefile 說明 Docker 建置指令及日常操作，因此將 build、rebuild、up、down、stop、restart、logs、ps、config、test、smoke、clean 統一收斂到 `shop-web/Makefile`。

## 2026-09-12｜路徑與分支約定

- 實際程式碼放在 `shop-web/`。
- 變更紀錄與討論紀錄現放在 `shop-web/docs/`；舊路徑 `shop-web-eample/docs/` 是前期指定位置，保留作歷史脈絡。
- 實作分支為 `feat/simple-shop`；需求流程包含完成後將成果添加到 `main`。

## 2026-09-12｜實作決策

- 前端採 React 19 + Vite，使用瀏覽器 `localStorage` 暫存購物袋。
- 後端以 Pydantic 驗證訂單欄位，並以伺服器端商品目錄與價格計算訂單總額。
- SQLite 檔案位於 `/data/shop.db`，掛載到 Compose 的 `shop-data` named volume；`make down` 保留資料，`make clean CONFIRM=yes` 才移除資料。
- 對外預設入口為前端 `localhost:8080`，後端 API 文件為 `localhost:8000/docs`；瀏覽器的 `/api/` 請求由 Nginx 轉送至 backend。

## 2026-09-12｜驗證結果

- `make up` 成功建置並啟動 `frontend`、`backend` 兩個 containers，`docker inspect` 顯示兩者均為 healthy。
- `make smoke` 通過首頁與經 Nginx proxy 的 `/api/health`。
- 直接執行 `docker exec nightwatch-shop-web_backend_1 python -m unittest discover -s tests -v` 顯示 4 tests OK；`make test` 也 exit 0，但本機舊 experimental Compose 不輸出測試明細。
- backend 重啟前後確認相同四筆測試訂單仍在，proxy health 也維持 OK。
- 前端 production build 成功；未做瀏覽器自動化互動測試。
- 詳細驗證結果已更新至 `CHANGELOG.md`。

## 2026-09-12｜協作時間線

- 初版由 main agent 在使用者補充協作要求前建立。
- 使用者補充協作要求後，改由 subagent `gpt-5.6-luna max` 接手實作、檢查與修正。
- main agent 統籌整合驗證，完成後依需求將 `feat/simple-shop` 成果添加到 `main`。

## 2026-09-12｜本次整合紀錄

- 使用者同意繼續 `shop-web/` 並忽略既有 `shop/`；原有 `shop/` 等遠端內容保留，文件現放 `shop-web/docs/`，先前版本曾使用 `shop-web-eample/docs/`。
- main agent 從 `origin/master` 的 `e12599a` 建立 `feat/integrate-shop-web`，再以 `--allow-unrelated-histories` 成功整合本機 `main`；雙方原本的獨立歷史均保留。
- 整合當時尚未 push；後續已推送 `master` `4808338`，遠端 `main` 已移除。

## 2026-09-12｜文件搬移與後續流程

- 在 `chore/shop-web-docs` 分支將兩份文件從歷史路徑 `shop-web-eample/docs/` 搬至現行路徑 `shop-web/docs/`，並移除空的舊目錄。
- README 與文件連結已同步改用 `shop-web/docs/`；舊路徑只作歷史脈絡。
- 後續功能改動從最新 `master` 開功能分支，PR 目標為 `master`，經 reviewer review 後才合併。

## 2026-09-12｜本次 backend 功能與交付方式

- 本次 demo 維持兩個 containers 與唯一入口：frontend 對外使用 `8080`，backend 對外使用 `8000`；完整 backend 功能細節見 [Backend CHANGELOG](../backend/CHANGELOG.md)。
- 使用者要求由 subagents `gpt-5.6-luna max` 執行功能開發與檢查，完成後只建立 PR 交由專責 reviewer 審查，不由執行 agent 自行 merge。

## 2026-09-12｜三張故障卡與事件頁定案

- 專案目的為觀察服務問題，因此在既有兩個 containers 與既有購物流程上增加可恢復的 backend 故障演練；前端控制頁固定使用 `/#/events`，不引入 router 或其他依賴。
- 三張卡的固定 ID 為 `checkout_exception`、`database_write_lock`、`checkout_delay`。前者覆蓋 `POST /api/orders` 與購物車 checkout，交易例外應回傳 500 並 rollback；中者覆蓋 SQLite 寫入並在最多 10 秒等待後失敗；後者讓兩條結帳路由延遲 10 秒。
- 事件頁只透過 `GET/POST/DELETE /api/demo-faults` 控制；POST 使用 `{fault_id}`，成功回應含 `cards`、`active`、`lease_seconds`、`delay_seconds`。`active` 的 `activating`、`active`、`restoring` 狀態與剩餘時間由 server 決定，前端約每 2 秒同步；任何 active 或轉換中的狀態都禁止啟用其他卡，操作 pending 時禁止重送。
- 啟用後使用者回到購物頁手動操作，頁面不自動造訂單；server lease 60 秒後自動解除，也提供 DELETE 手動解除。事件頁離開時清理輪詢，兩個服務入口仍為 `http://localhost:8080/` 與 `http://localhost:8000/`。
- 本次 production Docker build 已由 `make up COMPOSE=docker-compose` 驗證；既有 10 tests、首頁與 health smoke 均通過。真 HTTP 驗證涵蓋 cards/busy 409、兩個 checkout 入口的 exception rollback、SQLite lock 約 10.18 秒 500 與 DELETE 釋放、delay 手動解除約 0.83 秒喚醒，以及剩餘約 8.64 秒時 delay 的 TTL 自動喚醒；restart 後 fault inactive 且寫入恢復，log 含 traceback/status 500/fault ID。
- Chrome CDP 已以真實 DOM 驗證啟用/解除並取得 desktop/mobile 截圖；visual review 確認 desktop 三卡完整、390px mobile 單欄無溢出，`main.py`/`demo_faults.py` workspace 與 container hash 一致，前端部署 asset 為本次 production build。未宣稱三張卡均完成自動到期驗證，`database_write_lock` 的 60 秒自動到期尚未單獨驗證。主機直接 `npm run build` 仍因缺少 `vite` 失敗，Docker production build 已成功。

## 2026-09-12｜微服務拆分定案與目前狀態

- 服務固定為五個 containers：`frontend`（Nginx）、`backend`（gateway）、`catalog`、`cart`、`order`。前端 `8080` 與 gateway `8000` 是唯一 host ports；三個業務服務只在 Compose network 使用 `8000`。
- `shop-data` 保留為 read-only legacy source；catalog/cart/order 各自初始化並匯入所屬表到 private data volume，後續由各自 SQLite 負責。`make up` 先停舊 `frontend/backend` 入口，避免 migration 前舊單庫繼續寫入。
- 前端以 `daily-cart-id` 對應 server cart，舊 `daily-cart` 僅在成功逐項匯入後清除；商品缺少時顯示錯誤並保留舊內容。cart checkout 使用 `/api/carts/{cart_id}/checkout` 與 `Idempotency-Key`，HTTP 失敗可同 key 重試，未知結果時鎖定修改避免重複訂單。
- checkout 的跨服務一致性採 order coordinator 的 cart prepare、order commit、cart complete/abort 與 restart reconcile；商品刪除採 cart lazy prune。三張故障卡固定作用 order，catalog/cart 保持可觀察。
- 本輪文件與設定已完成，`docker-compose ... config` 已解析成功；production build、10 tests、smoke、migration/故障/recovery/idempotency/瀏覽器驗證尚待實際五服務完成後補記，不能沿用歷史兩容器結果作為本輪證據。

## 2026-09-12｜故障卡改為手動解除

- 最新決定移除 60 秒自動恢復；三張 fault card 持續啟用，只有 `DELETE /api/demo-faults` 解除。`lease_seconds`、active 的 `expires_at`、`remaining_seconds` 使用 `null`，delay 每次 checkout request 仍等待 10 秒。
- 事件頁保留約 2 秒 server polling、active/transition 時禁止其他卡，並改顯示「持續啟用，需手動解除」；本輪不再執行舊 TTL 驗證，待 backend manager 更新後重建整合環境。

## 2026-09-12｜微服務整合實測補記

- `make up COMPOSE=docker-compose` 完成五服務 production build/recreate，`frontend`、`backend`、`catalog`、`cart`、`order` 均為 `Up (healthy)`；對外仍只有 `8080`（frontend）與 `8000`（gateway）。
- 遷移前 legacy checkpoint 與首次匯入後均為 products 6、carts 0、cart_items 0、orders 34；後續實測建立資料，最後 volume inspection 為 products 6、carts 1、cart_items 1、orders 43。
- 既有 10 tests 以 `make test COMPOSE=docker-compose` 全數通過（68.038 秒），首頁與 gateway health 的 `make smoke COMPOSE=docker-compose` 通過；`uv lock --check` 與 requirements export consistency 也通過。六張商品圖從 production Nginx 逐張取回 HTTP 200。
- fault recovery、節點故障、checkout retry 與真瀏覽器互動等待獨立驗證結果，本段沒有預先宣稱通過。先前 service-stop 檢查曾觀察到 catalog 停止時 cart GET 回 500，且 catalog/cart `/logs` 沒有 `app.log`；最終 backend snapshot 若未修正，需在 PR 前處理或列為 blocker。

## 2026-09-12｜接手驗證結案

- 上述 cart GET 500 與 catalog/cart 缺少 app.log 已修正並在真實容器中驗證。另修正前端失敗重試遺失 disabled 欄位內容、abort 早於 prepare 的恢復問題。
- 三張卡各自等待超過 65 秒仍 active，手動解除後恢復；完成真實 SQLite lock 下的 commit/complete 失敗恢復，以及 order 重啟恢復。既有 10 tests、production build、smoke、Chrome 購物車匯入與重試皆通過，詳見 CHANGELOG 最後一節。
- 使用者要求 README/CHANGELOG 同步更新，提交前先 pull 最新 master 並 merge 到目前分支，PR 交由專責 reviewer 審查，不自行合併。
