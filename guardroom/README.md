# Guard Room 啟動與設定

HTTP server 實作在 [control/server/](../control/server/README.md)；本目錄提供 Docker Compose、拓撲設定與啟動腳本。整套 Shop／Guard Room／Console 可由 repo 根目錄 `./restart.sh` 啟動，操作方式見 [根目錄 README](../README.md)。

## Docker 操作與資料保存

`./guardroom/restart.sh` 現在建置映像、驗證 config、重建容器並等待健康檢查。
映像使用鎖定的 server dependencies，不需 host 安裝 Python 或 uv。
預設 Compose project 為 `nightwatch-guardroom`，不影響 shop-web 或其他 Compose project。

```sh
./guardroom/restart.sh          # build + recreate，保留資料
./guardroom/restart.sh --open   # 使用現有映像啟動，不重新 build
./guardroom/restart.sh --close  # 停止，保留容器與資料
docker compose -p nightwatch-guardroom -f guardroom/compose.yaml ps
docker compose -p nightwatch-guardroom -f guardroom/compose.yaml logs -f guardroom
curl http://127.0.0.1:9999/health/ready
```

Host 預設僅發布 `127.0.0.1:9999`，container 內監聽 `0.0.0.0:9999`。
可用 `PORT=10000 ./guardroom/restart.sh` 修改 host port；container 內仍是 9999。
Client 與 HTTP monitor 若使用自訂 port，也須明確設定 URL。

| 掛載 | 用途 |
| --- | --- |
| host `guardroom/shop-web.config.json` → `/app/guardroom/shop-web.config.json`，唯讀 | 拓撲與聚合設定；可用 `GUARDROOM_CONFIG` 指定其他 host config |
| host `control/tmp/` → `/app/control/tmp/`，唯讀 | 共用 Shop 的 JSONL；可用 `MONITOR_LOG_DIR` 指定已存在的 host 目錄 |
| named volume `nightwatch-guardroom_guardroom-state` → `/app/guardroom/.run/` | live checkpoint、歷史 snapshots、investigations.sqlite3 |

Config 在容器內的位置固定，相對路徑以 `/app/guardroom/` 解析。自訂 config 建議保持
`monitor_log=../control/tmp/monitor.jsonl`、`snapshot_path=.run/...`、`history.directory=.run/...`；
若使用其他容器路徑，必須自行增加對應掛載與寫入權限。
Config 修改後重啟。舊 host 程序及 `guardroom/.run/`、`control/.data/` 資料不會自動遷移，
從舊部署切換前請先停止舊程序並備份、遷移所需資料。

容器重啟／重建或 `docker compose down` 不會刪除 named volume；
**不要執行 `down -v` 或刪除該 volume，否則會刪除 graph 與調查資料，無備份無法恢復。**
歷史快照仍依 retention 設定自動到期清理。不同 instance 可用不同的
`GUARDROOM_PROJECT_NAME` 與 host port，避免共用 state volume。

調查需要的 `NIGHTWATCH_LLM_API_KEY`／`OPENAI_API_KEY`、`NIGHTWATCH_LLM_MODEL` 等設定
可透過 shell export 或 `guardroom/.env` 提供；不會自動載入 host 的 `control/.env`。
預設 investigation graph URL 為容器內 `http://127.0.0.1:9999/api/graph`。
不要將 API key 寫進 Dockerfile 或提交 `.env`。

容器以 UID 10001 非 root 執行，根檔案系統唯讀，僅 state volume 與 `/tmp` 可寫；
停用額外 Linux capabilities、禁止權限提升，並限制 512 MiB 記憶體、1 CPU、128 processes。
`restart: unless-stopped` 處理程序退出後的重啟；Docker daemon 停止期間無法提供服務。

`/health/ready` 檢查 live checkpoint 最近 10 秒內有成功提交，且 history loop 在
`max(10, 3 × history.interval_seconds)` 秒內成功執行。正常回 200，逾時回 503。
Shop 節點 warning／failing 不會令 Guard Room unhealthy。
Compose 每 5 秒探測一次；**unhealthy 本身不會觸發自動重啟**，此版未加入 autoheal。

現有 Compose 未注入 `NIGHTWATCH_SHOP_URL`，修復工具預設關閉。容器內 localhost 不是 host 的 Shop；目前修復工具只允許本機 HTTP origin，不能直接改填其他容器名稱。本機 CLI／host server 的啟用方式見 [修復設計](../control/SYSTEM_DESIGN.md)。

## 監測拓撲與故障判定

```text
shop.health → shop.db.query
shop.products（gateway → catalog）
shop.checkout.request → shop.checkout.logic → shop.db.write
```

設定有六個節點、三條連線。Gateway 觀測 health 與商品列表，order 的 `OrderStore._health_sync()` 觀測 `shop.db.query`；商品列表走 catalog，不連到 order DB query。`shop.order.health` 事件仍未映射為獨立節點；catalog／cart 的內部操作尚未完整監測。

### 結帳故障觀測

`POST /api/orders` 與 `POST /api/carts/{cart_id}/checkout` 共用以下 monitor：

| Monitor | 範圍 | latency warning（min_samples=1） |
| --- | --- | --- |
| `shop.checkout.request` | gateway middleware，包含等待 order 的時間與 HTTP 回應狀態 | 1000ms |
| `shop.checkout.logic` | order 的 `OrderService._checkout()`，包含 catalog／cart 呼叫、重試與補償 | 1000ms |
| `shop.db.write` | 結帳期間 order SQLite 的 `BEGIN IMMEDIATE`、寫入 SQL、commit／rollback | 500ms |

故障延遲在 order middleware 執行，位於 logic monitor 外層，因此 10 秒等待只計入
gateway request。Gateway 以內部 `X-Nightwatch-Parent-Invocation` header 傳遞自己產生的
invocation ID；order 接收後讓 logic 關聯到 request，再用 `copy_context()` 將關聯帶進
DB executor。此 header 不採用外部使用者提供的值，也不作為授權或去重依據。

啟用故障後，需送出有效結帳請求。以下為窗口內只有該次結帳觀測時的結果：

| 故障 | Request | Logic | DB write |
| --- | --- | --- | --- |
| 10 秒延遲 | warning（耗時超標，errors=0） | ok | ok |
| 結帳例外 | failing | failing | ok（成功 rollback） |
| DB 寫入鎖逾時 | failing | failing | failing |

若窗口混有成功與失敗，對應節點為 warning；故障解除後，舊事件仍保留到 60 秒窗口過期。
新版故障卡不會自動到期，演練後需 `DELETE /api/demo-faults` 手動解除。
預期的 HTTP 4xx（空購物車、商品不存在、驗證失敗）不計服務失敗；
正常回傳的 HTTP 5xx 也會計入 request 失敗，不必有未處理例外。
業務例外不計為 DB 失敗；SQLite 本身的寫入、commit 或 rollback 例外才計入。
DB write 的 traffic／p95 以單次 DB 操作為單位，一次結帳可產生多筆樣本，並非訂單數。
DB write 不含 catalog／cart DB、啟動遷移或背景 reconcile。購物車結帳是跨服務補償，
不是單一 DB transaction；失敗時還需確認 cart reservation 釋放，並以同一
`Idempotency-Key` 驗證重試不重複建單。訂單提交後的 cart complete 失敗會記為 logic／
request 失敗，order DB 仍可為 ok。
購物車／商品 CRUD 尚未納入這三個結帳 monitor。

## 60 秒窗口與資料保存

Monitor 送出單次呼叫的狀態、耗時與 log；Guard Room 每秒讀取共享 JSONL 的完整新行，按 [設定](shop-web.config.json) 映射與聚合。`finished`／`exception` 才計入完成次數，普通 ERROR log 不直接改健康。

| 窗口內完成事件 | 節點狀態 |
| --- | --- |
| 沒有完成事件 | unknown，沒有新訊號，不表示恢復或服務死亡 |
| 全部失敗 | failing |
| 部分失敗 | warning |
| 全成功且樣本數達門檻、p95 達延遲門檻 | warning |
| 其餘全成功 | ok |

預設窗口為 60 秒，無新請求時舊觀測會過期。商品列表 latency 門檻為 500ms／20 個樣本；結帳門檻見上表。沒有達到延遲判定樣本數仍可顯示 p95。Edge 只來自 config，沒有邊流量量測；`observed=false`、量測為 null。`alive` 只表示窗口內有事件。

JSONL writer 使用 POSIX flock 協調本機多程序寫入，消費端等待完整換行。Checkpoint 保存近期最多 10000 筆事件、去重資訊與讀取位置；高流量時窗口內事件可能提前淘汰。Config 路徑相對於 config 所在目錄，修改需重啟。

預設每 5 秒保存一張歷史 snapshot，保留 900 秒。`GET /api/graph/snapshots` 以 `limit`／`before_seq` 分頁，`GET /api/graph?timestamp=...` 讀不晚於指定時間的最後一張保留快照；找不到回 404。Graph 的 `at` 使用台灣時間 +08:00，Monitor 與 SSE `server_now` 使用 UTC。

Console 透過調查 state／stream 取得 graph、歷史調查與報告，另以 `/events` 讀即時 log。Live `/events` 也包含相容舊畫面的 state／graph／incident；每 2 秒的 ping 含 `server_now`。Monitor log 不提供歷史續傳；調查串流有獨立 cursor。

HTTP 接收使用 `POST /api/logs`，格式以 [logs.py](../control/server/logs.py) 為準；不是 OTLP receiver。必須使用單一 uvicorn worker，不同 instance 不可共寫 state。API 無身分驗證，預設僅發布 localhost。詳細端點見 [server README](../control/server/README.md)，Monitor 使用方式見 [monitor README](../control/monitor/README.md)。
