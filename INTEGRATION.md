# 對接與 mock 盤點

盤點日期：2026-09-12。以下以本次讀到的程式碼與隔離環境驗證為準，沒有沿用舊契約的完成宣稱。

## 做了什麼

**目前能完成「店面請求 → 部分 Monitor → Guard Room → 調查／報告」，還不能完成「故障 → 調查 → 批准 → 真實修復 → 驗證」。**

### 已有真實實作

| 項目 | 程式實際行為 | 程式依據 |
| --- | --- | --- |
| 商品、購物車、訂單 | React 呼叫 gateway；gateway 經 HTTP 呼叫 catalog／cart／order，SQLite 保存狀態，checkout 有冪等與恢復流程 | [gateway](shop-web/backend/app/main.py)、[order](shop-web/backend/app/order.py)、[前端](shop-web/frontend/src/main.jsx) |
| 店面故障演練 | `GET/POST/DELETE /api/demo-faults` 由 gateway 轉送 order；三張卡真的造成結帳例外、SQLite 寫入鎖、10 秒延遲 | [demo_faults.py](shop-web/backend/app/demo_faults.py) |
| Monitor 與 graph | decorator 產生 JSONL；GraphStore 讀檔、按 monitor 設定投影；`GET /api/graph` 預設是真實來源 | [monitor](control/monitor/__init__.py)、[graph_state.py](control/server/graph_state.py) |
| 歷史快照 | `GET /api/graph/snapshots` 與 `GET /api/graph?timestamp=...` 讀保存的快照 | [graph_history.py](control/server/graph_history.py)、[main.py](control/server/main.py) |
| 日誌 | `POST /api/logs`、`GET /api/debug/logs`、`/events` 的 log 事件已有實作；店面目前主要經共享 JSONL 接入 | [logs.py](control/server/logs.py)、[frontend_live.py](control/server/frontend_live.py) |
| 調查 | `/api/investigations` 建立、列表、state、stream、detail、events、report、snapshots、context、export 都有處理程式，資料保存於 SQLite | [investigation_api.py](control/server/investigation_api.py)、[investigation_store.py](control/server/investigation_store.py) |
| 自動偵測 | 三次連續新鮮 warning／failing 觸發調查，SQLite 旗標防止同一段異常重複開案 | [detector.py](control/server/detector.py)、[manager](control/server/investigation_api.py) |
| Agent 工具 | live 模式讀 graph、歷史索引、節點詳情與近期 log，再提交結構化報告；真模型失敗不自動退回錄影 | [graph.py](control/nightwatch_agent/graph.py)、[loop.py](control/nightwatch_agent/loop.py) |
| Console live 工作區 | 建立調查、讀 state／detail／events／context、接收兩條 SSE；上游失敗會顯示錯誤 | [investigations.js](console/src/investigations.js)、[serve.py](console/serve.py) |

### 尚未對接／未實作

| 優先 | 缺口 | 實際結果與下一個接點 |
| --- | --- | --- |
| 高 | control → 店面故障控制 | `LiveStore.execute()` 一律 503。店面有 `/api/demo-faults`，但 control 沒有呼叫它；前端 proxy 也只允許建立調查 POST。需要先串接真實故障操作、錯誤與生命週期。 |
| 高 | 拆服務後的監測拓樸 | [設定](guardroom/shop-web.config.json) 仍宣告 `shop.db.query`，現行店面程式沒有此 monitor；order 發送 `shop.order.health` 卻沒有 node 對應。cart／catalog 只有一般 request log，結帳也沒有對應 Monitor。店面的故障不保證會被目前三個節點觀察到。 |
| 高 | 批准、修復、修復驗證 | 沒有真實 actuator，也沒有修復後觀測窗口、可信基線或根因稽核。mock 的 recovered 不能代表服務已修復。 |
| 中 | 完整實驗報告 | 舊 incident report／timeline 在 live 回 503；已有 investigation report 是調查結果，沒有注入真相、基線比較、修復驗證。 |
| 中 | 指標／trace／health | Prometheus 與 Jaeger 固定不可用；saturation=null，trend=na，edge 量測=null、observed=false。alive 只是窗口內有 monitor 事件，沒有獨立 health probe 或合成顧客訂單率。見 [GraphStore](control/server/graph_state.py)。 |
| 中 | readiness／capabilities | readiness 永遠 ready=false；除 logstore 外都是未接／未探測，model.available=false 不代表模型一定不可用。capabilities.tools/actions 為空、budget 為零，尚未反映 investigation 的真實工具。見 [LiveStore](control/server/frontend_live.py)。 |
| 中 | 全歷史日誌 | `search_logs` 只篩選最近抓回的有限批次；checkpoint 最多保留所有節點合計 10000 筆，沒有完整歷史分頁。不能從查無資料推論沒有故障。 |
| 低 | 對話、OTLP、Grafana 告警 | 沒有 chat、OTLP receiver 或 Grafana webhook 路由；目前日誌入口是 `/api/logs`。這些是未實作，不是正在用 mock 回應。 |
| 低 | 前端的專用報告／匯出入口 | 後端 `report`／`snapshots`／`export` 已存在，但目前 console 主要讀 detail／events／context，沒有獨立匯出操作。 |

`shop.order.health` 未映射的日誌仍可被接收，但不會投影成目前設定中的節點；`/api/debug/logs` 又是按 node refs 展開，因此也無法經該節點查詢路徑看到它。

### 路由存在但 live 只有 503 的 API

依據 [frontend_api.py](control/server/frontend_api.py) 的路由與 [frontend_live.py](control/server/frontend_live.py) 的 handler；下列 11 條目前只有 control mock 模式能提供相應模擬行為：

| 方法 | 路徑 |
| --- | --- |
| GET | `/api/faults/catalog` |
| GET | `/api/faults/instances` |
| POST | `/api/faults` |
| POST | `/api/faults/instances/{id}/restore` |
| POST | `/api/faults/restore-all` |
| POST | `/api/rounds` |
| GET | `/api/rounds/operations/{id}` |
| POST | `/api/incidents/{id}/approve` |
| POST | `/api/incidents/{id}/abort` |
| GET | `/api/incidents/{id}/report` |
| GET | `/api/incidents/{id}/timeline` |

最後兩條需要已存在的調查 ID，未知 ID 先回 404。`abort` 不會中止真實 investigation worker。`/api/state.faults` 雖回空陣列，也不能據此宣稱店面沒有作用中的故障。

以下路由完全沒有註冊：`GET /api/graph/history`、`POST /api/chat/messages`、`POST /internal/otlp/v1/logs`、`POST /internal/alerts/grafana`。Graph 歷史目前使用 snapshots／timestamp API。

### 仍保留的 mock／錄影

| 開關或入口 | 假資料範圍 | 為何保留 |
| --- | --- | --- |
| `NIGHTWATCH_MOCK_DATA=1` | [frontend_mock.py](control/server/frontend_mock.py)：記憶體故障卡、事故、批准與修復結果，回 `X-NightWatch-Mock: true` | 明確使用的舊前端 API 演練；不操作真實店面。它不會把 `/api/investigations` 換成假模型。 |
| `GET /api/graph?state=normal/problem` | [dummy_graph](control/server/main.py)：固定十節點與固定數字 | 明確預覽入口，未帶 state 的 graph 仍讀 monitor。 |
| `python3 console/serve.py --mock` | [mock_control.py](console/mock_control.py)：錄影模板與定時狀態變化 | 離線 UI 演示；建立調查回 503。 |
| `/?source=recording` | [data.js](console/src/data.js) 播放 `catalog_pool_leak` 錄影 | 使用者可明確切換的歷史演示，建置仍會複製錄影。 |
| Agent `--fixture`／`--replay-model` | [replay.py](control/nightwatch_agent/replay.py)：錄影工具，後者另用腳本模型 | 離線演練與既有測試依賴，沒有自動 fallback。 |
| 示範結帳 | 真實保存訂單，但沒有金流、扣款或物流 | 產品範圍本來就是本機購物 demo；API 資料庫行為不是 mock。 |

店面 UI 的三張故障卡文字是靜態定義，狀態與操作仍走真實 API；這與 control mock 的五張舊 OTel 卡是兩套功能。

### 本次清理

- 移除舊 OTel 契約說明、舊編號任務／封存 stub、過期實作規格、重複變更日誌與 root Hello World 程式。
- 移除不再對應現行服務的三個契約檢查腳本及其舊 manifest／cards／adapter、未使用範例和 console schema 草稿；保留程式與既有測試仍載入的 schemas、錄影、monitor log schema 及 agent-report 範例。
- 重寫 root、control、server、console、guardroom README，啟動方式與限制直接來自目前程式。
- 移除 `install_frontend()` 已沒有呼叫端的空 store／log-only 分支；抽出共用 UTC 時間函式，讓 live 不再依賴 mock 模組取得時間。
- 假設：仍有明確入口或測試使用的 mock／錄影不屬於廢碼，因此保留。現行 shop API 文件、Monitor 使用說明與 gate 工具仍有用途，也保留。
- 本次列出缺口，沒有實作自動修復或更改店面監測範圍。既有 gate 修改、未追蹤紀錄與資料未納入本次提交。

## 依據什麼驗證的

- `rg` 與逐段讀程式，核對 router、HTTP client、前端 fetch／SSE、環境開關、Monitor 發送點及 JSONL 設定；以上表格的程式連結是主要依據。
- `PYTHONPATH=control control/.venv/bin/python -m unittest discover -s control/tests -v`：23 tests，OK。涵蓋框架與 Monitor 行為；模型部分使用替身。
- `control/server/.venv/bin/python -m unittest discover -s shop-web/backend/tests -v`：10 tests，OK。既有 harness 啟動四個真實服務與暫存 SQLite，包含商品／購物車／checkout 冪等及並發。
- `python3 console/build.py`：13 個檔案建置完成。`npm --prefix shop-web/frontend run build`：Vite production build 成功。
- `control/server/.venv/bin/python -u - <<'PY'` 執行隔離 HTTP 驗證：啟動四個真實店面服務、live／mock control、兩種 console，使用暫存 SQLite 與實際店面 Monitor JSONL。商品、health、graph、log、state、快照列表及 OpenAPI 均 200；`shop-health`／`shop-products` 有觀測，`shop-db` 為 unknown。兩條 SSE 收到 state／graph／ping。
- 同次 HTTP 驗證確認上述 11 條 live 缺口回 503、4 條未註冊路由回 404；沒有金鑰的調查從 202 轉為 execution_failed，report／snapshots／events／context／export 可讀。這只驗證失敗結果保存，不是成功模型調查。
- 同次驗證確認 console 從建置產物提供首頁／JS 並代理調查 state／SSE；不允許的故障 POST 回 405。control mock 的注入／批准回 202、報告 200；console mock 可讀 state／錄影、收到 SSE，但建立調查回 503。店面故障狀態前後相同。最後輸出 PASS 與 All verification services stopped。
- `git diff --check` 無輸出；Python 檢查現行文件的本機 Markdown 連結，缺失為 0。

## 沒有驗證的

- 未呼叫真實模型，本次只驗證缺少金鑰時會保存失敗結果；替身模型測試不能代表真模型通過。
- 未做瀏覽器互動、Docker Compose／Nginx 部署、真實故障卡注入或長時間自動 detector 復原流程。
- HTTP 調查報告／context／export 本次只讀到缺少模型金鑰的失敗紀錄，沒有驗證成功模型對話、非空報告 evidence、歷史 timestamp 查詢或 `/api/logs` 的 HTTP 寫入。
- mock 批准／報告與錄影驗證僅證明演練功能可執行，不代表真實修復；未實作項目沒有成功路徑可驗。

## 契約疑問

無。依本次要求，以程式為準；仍保留的 JSON schema 是執行與測試依賴，不作為功能已對接的證明。
