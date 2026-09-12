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
- Chrome CDP 已以真實 DOM 驗證啟用/解除並取得 desktop/mobile 截圖；最終截圖 hash/visual review 尚待核對。未宣稱三張卡均完成自動到期驗證，`database_write_lock` 的 60 秒自動到期尚未單獨驗證。主機直接 `npm run build` 仍因缺少 `vite` 失敗，Docker production build 已成功。
