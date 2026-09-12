# Guard Room：config → monitor log → graph snapshot

以 `shop-web/backend/app` 為範例，單一程序、檔案儲存。API 回傳符合
`console/schema-draft/graph.schema.json` 的資料實例，不改寫 schema 定義。

本文件是 Guard Room 功能、設定與操作的主要說明。FastAPI 的本機開發方式及程式模組分工
見 [Server 開發入口](../control/server/README.md)。

Monitor 送出的是單次執行的 status／duration_ms 與函式內 log，Guard Room 計算 graph 的
p95／errors／traffic／status。Logger 擷取與輸出方式見
[monitor README](../control/monitor/README.md)；拓撲與延遲門檻設定在
[shop-web.config.json](shop-web.config.json)。

## 整個流程

1. **讀 config**：啟動時載入 `guardroom/shop-web.config.json`，可用
   `GUARDROOM_CONFIG=/absolute/path/config.json` 指定。驗證 monitor ID／node ID 唯一，
   edge 兩端必須是已宣告的 monitor ID，拒絕重複 edge；修改後需重啟。
2. **建立拓撲**：每個 monitor 對應一個 node；config edge 的 monitor ID 轉成 node ID。
   沒有事件的節點仍存在，status=unknown、指標=null。
3. **恢復狀態**：讀取 checkpoint 的 snapshot、近期事件與 JSONL 讀取位置，
   按目前 config 重建拓撲；依 `(monitor_id, event_id)` 去重。
4. **收事件**：啟動先讀一批，此後每秒接續讀 `monitor_log`，每批最多 1000 行。
   同時支援 `POST /api/logs`，共用去重和更新流程。
   支援 monitor 原始 JSONL 與 console log JSONL。不完整末行等下次；
   缺少 event ID 等舊格式／壞行記 warning 後略過。換 inode 或檔案縮短時從頭讀。
5. **彙總節點**：依事件原始時間計算最近 `window_seconds`（預設 60 秒）的
   完成次數／秒、失敗比例、duration 的 nearest-rank p95。
   finished／exception 才算完成；started 和一般 log 不重複計次。
   全失敗=failing、部分失敗=warning、無完成資料=unknown。
   全成功時，若已設定 latency 門檻且樣本足夠、p95 達門檻則為 warning，其餘為 ok。
   一般 ERROR log 不直接改健康，assessment 保持 unassessed。
6. **持久化**：snapshot、近期最多 10000 筆事件、讀取位置一起寫暫存檔，
   flush／fsync 後 atomic replace；成功才更新記憶體並廣播 SSE log。
   每秒更新窗口，即使沒有新事件也會讓舊觀測過期，seq 每次提交遞增。
7. **讀 graph**：`GET /api/graph` 回傳已提交 snapshot；Console 透過
   `/api/state` 與 `/events` 取得 live 狀態、graph 及 log，端點說明見
   [Server README](../control/server/README.md)。

Edge **只來自 config**，不從 parent invocation 推測。尚無 edge 量測，
rps／errors／p95_ms=null、observed=false。saturation／trend 分別為 null／na。
alive 代表窗口內有事件，不等同服務探活，閒置服務也可能為 false。
logstore age 使用最新已知 monitor 的事件時間；Prometheus／Jaeger 未接入，ok=false。

未知 monitor 的 log 仍保存並廣播，refs.node_ids=[]，不新增節點。
已知 monitor 的 refs 由 config 映射。最近 10000 筆同時是去重和聚合容量，
高流量時窗口內資料可能提前淘汰。

## shop-web 範例

```text
shop.health   → shop.db.query
shop.products（gateway → catalog）
shop.checkout.request → shop.checkout.logic → shop.db.write
```

Gateway 的 `health()`、`products()` 觀測探活與商品列表；order 的
`OrderStore._health_sync()` 以 `shop.db.query` 觀測 order DB 讀取。
商品列表已改由 catalog 提供，不再連到 order DB query 節點。結帳另有下述三層。
沿用 monitor JSONL sink 與
Compose 的 `control/tmp/monitor.jsonl` 共用掛載，無需額外 HTTP bridge。
monitor 不需要填 node_ids，由 Guard Room config 映射。

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

### 每個 monitor 的 latency warning

在 monitor 定義中可選填 `latency`，由 Guard Room 計算並判定，shop-web 不需自行計算 p95：

```json
{
  "monitor_id": "shop.products",
  "node_id": "shop-products",
  "kind": "service",
  "latency": {"warning_ms": 500, "min_samples": 20}
}
```

範例 config 先替商品列表啟用上述值，作為 demo 初始門檻，並非實測 SLO。
Health 和 DB query 尚未設定 latency，維持既有錯誤比例判定。
`warning_ms` 必須是有限正數（毫秒），`min_samples` 必須是正整數；修改 config 後重啟生效。

沿用 `window_seconds`（預設 60 秒）：只有 finished／exception 事件的有效 duration_ms
才算耗時樣本，忽略缺值、非數字、非有限值及負值。樣本足夠且 p95 **大於或等於**
warning_ms，會將原本全成功的 ok 提升為 warning；既有 failing／warning 不會被降級。
Started 和一般 logger 訊息不計入樣本數；沒有完成事件仍為 unknown。
樣本不足時仍顯示可計算的 p95，但不觸發 latency warning。未設定 latency 或設為 null
表示不啟用延遲判定。各 monitor 僅使用自己的事件，不沿 edge 傳播健康狀態。

此版使用絕對門檻，沒有基準比較、持續時間或不同的恢復門檻；下一次重算時，若 p95
低於門檻或樣本數不足，就回到既有錯誤比例判定。低流量 monitor 應另調整 min_samples，
例如每 10 秒一次的 health 在 60 秒內約只有 6 筆，不適用 20 筆門檻。
判定寫進 live／歷史 snapshot 的既有 status 欄位；目前沒有另外新增結構化 warning 原因。
歷史 graph 保留當時結果，不因後續調整門檻重算。

每次 graph 更新依下表判定 node.status，第一個符合的條件生效：

| 條件 | node.status |
| --- | --- |
| 窗口內沒有完成事件 | unknown |
| 完成事件全部失敗 | failing |
| 完成事件部分失敗 | warning |
| 全成功、latency 已啟用、有效樣本數 ≥ min_samples、p95 ≥ warning_ms | warning |
| 全成功，其餘情況 | ok |

例如商品列表窗口內有 20 筆有效成功樣本，p95=650ms、門檻=500ms，
`GET /api/graph` 會包含以下節點欄位（僅節錄，不是完整 graph）：

```json
{"id": "shop-products", "p95_ms": 650, "errors": 0, "status": "warning"}
```

同樣耗時但只有 19 筆有效樣本時，status 仍為 ok；這不代表延遲已被充分驗證。
Monitor 的單次 status=ok 不受影響，因為函式執行結果與窗口健康判定分開處理。
P95 使用各 monitor 完成事件的耗時；checkout request 包含 middleware 等待，
logic／DB 則各自量測內層執行時間，均不是 edge 延遲或完整網路往返時間。

### Shop logger

Shop gateway／order 啟動時將 monitor handler 掛到 `shop` logger。Monitor 的 `level="WARNING"`
只收執行期間的 WARNING／ERROR／CRITICAL logger 訊息；DEBUG／INFO 不進 monitor，
應用程式的 stdout 和 `/logs/app.log` 仍依原本 `LOG_LEVEL` 設定輸出。
Logger 本身先過濾掉的訊息無法由 monitor 補回，例如 `LOG_LEVEL=ERROR` 會略過 WARNING。
`started`／`finished`／`exception` 生命週期事件全部保留，不受此門檻影響。

函式內的 warning／已處理例外可作為調查資訊；巢狀呼叫的 log 歸屬最內層 monitor。
Order 的結帳與補償錯誤會歸屬當時的 logic invocation。
未處理例外已由 monitor 自動捕捉，不需再記錄一次相同 exception。
啟停與非結帳 request middleware 的既有 log 不在 monitor invocation 內，仍只寫應用日誌；
Gateway 結帳 middleware 的 WARNING／ERROR 會歸屬 checkout request。

### 啟動與觀察

從 repo 根目錄執行（Guard Room 需 Docker Engine／Desktop、Compose v2、curl）：

```sh
./guardroom/restart.sh
./shop-web/restart.sh
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/products
# 等下一個一秒讀取週期
curl http://127.0.0.1:9999/api/graph
```

應有 shop-health、shop-products、shop-db、shop-checkout-request、shop-checkout-logic、
shop-db-write 六個節點和三條 edge；尚未呼叫結帳時，其三個節點為 unknown。
首次啟動從 log 開頭讀，舊資料多時需數個週期追上。上述單次 curl 只驗證資料流，
不會累積到商品列表 latency warning 所需的 20 筆，也不保證耗時超過門檻。
Shop backend 的 port 由 shop-web 設定，上例使用其預設 8000；若已設成 8001，
請對應修改 curl。Guard Room 改成 9999 不會改動 Shop 的 port。

## 更新頻率與多 monitor

| 路徑 | 更新時機 | 一批的定義 |
| --- | --- | --- |
| JSONL → live graph | 每輪等待 1 秒後讀取、重算並寫檔，即使無新事件也更新窗口 | 最多 1000 行，僅完整且有效的事件進入聚合 |
| HTTP → live graph | 每次 POST 接收並提交，無需等下一輪 JSONL | 一個 logs 陣列，1–50 筆事件，可包含多個 monitor |
| live graph → history | 啟動保存一份，其後約每 history.interval_seconds 保存 | 當時最新的完整 graph；預設 5 秒 |
| GET /api/graph | 只讀已提交的最新 graph | 不讀新 log、不重新計算、不觸發寫檔 |

實際週期間隔還包含處理與寫檔時間。GuardRoomSink 預設以 50 筆／0.5 秒組成 HTTP 批次，
詳細行為見 monitor README；一個成功函式呼叫至少有 started、finished 兩筆事件。

單一 worker 逐批執行完整提交，每個 node 僅使用自己 monitor_id 的事件；
每次仍重算整張 graph 並整份寫檔，其他 node 可能因窗口過期或共用容量淘汰而改變。
不沿 config edge 傳播 failing／warning。seq 每次成功提交加一，與事件數、歷史檔案數不同。

## Config 與檔案

`monitor_log`、`snapshot_path` 相對於 **config 所在目錄**，不依啟動 cwd。
本機開發的預設 checkpoint：`guardroom/.run/shop-web.graph-state.json`。
Docker 內位於 `/app/guardroom/.run/shop-web.graph-state.json`，保存在 named volume，
不是 host 的 `guardroom/.run/`。

| 欄位 | 用途 |
| --- | --- |
| version | checkpoint 版本 1 |
| snapshot | 符合 graph.schema.json 的 graph；亦為 GET /api/graph 回應 |
| recent_logs | 去重與恢復聚合的近期 console logs |
| file_cursor | log 路徑、device/inode、byte offset |

本機開發可用 `jq '.snapshot' guardroom/.run/shop-web.graph-state.json` 匯出純 graph；
Docker 部署可直接用 `curl http://127.0.0.1:9999/api/graph` 取得最新純 graph。
檔案含 log message，按應用日誌管理。checkpoint 損壞會讓啟動失敗，保留原檔，
不靜默清空；首次啟動 log 不存在則仍提供 unknown 拓撲。

## 歷史 snapshot 與台灣時間

新 graph 的 `at`（含最新狀態、歷史清單、歷史 graph）使用台灣時間 `+08:00`，
例如 `2026-09-12T13:10:00+08:00`。graph schema 同時接受舊 UTC `Z` 格式；
monitor 原始 log 的時間保持不變，聚合及歷史查詢依實際時間比較。

config 的 `history` 選填，預設如下；directory 同樣相對於 config 目錄：

```json
{
  "history": {
    "directory": ".run/snapshots",
    "interval_seconds": 5,
    "retention_seconds": 900
  }
}
```

啟動完成首次 log 讀取後保存一份，此後獨立排程約每 5 秒保存最新已提交的 graph，
保留 15 分鐘（約 180 份）。HTTP 每批仍更新 live checkpoint，但不觸發歷史存檔。
歷史的 `seq` 因此可以不連續；`at` 是該 graph 的產生時間，而非歷史寫檔時間。
停機期間不補造快照；若 live 更新停止，不重複保存同一個 seq。

每份存為 `.run/snapshots/snapshot-00000000000000000120.json`，僅含完整 graph，
不包含 recent_logs／cursor。先原子寫入，再加入查詢索引；啟動時掃描檔案恢復索引。
保留期限按目前時間計算，啟動及存檔時刪除過期的歷史快照檔案；此清理不可恢復。
查詢也會排除已過期但尚未清理的檔案。資料夾需專用，不能與 checkpoint、config 或
monitor log 混放；不同 instance 必須各自使用不同歷史目錄。
執行中歷史寫入失敗會記錄錯誤並在下一週期重試，不阻止 live graph 接收事件。

`GET /api/graph/snapshots?limit=100` 回傳索引，依 seq 由新到舊：

```json
{
  "snapshots": [
    {"seq": 120, "at": "2026-09-12T13:10:00+08:00"},
    {"seq": 115, "at": "2026-09-12T13:09:55+08:00"}
  ],
  "next_before_seq": 115
}
```

`limit` 為 1–500，預設 100；有下一頁才回 next_before_seq，否則為 null。
下一頁帶 `before_seq=115`，只取 seq 小於 115 的快照。沒有保留資料時回空陣列。

`GET /api/graph?timestamp=...` 取 **不晚於指定時間的最後一份歷史快照**。
例如查 13:09:58，以上例會回 13:09:55 的完整 graph，保留其原始 at、seq、拓撲與量測，
不套用目前 config 重算。相同時間取 seq 最大者。晚於最後一份歷史資料時回最後一份；
沒有符合的保留快照時回 404，detail.code 為 snapshot_not_found。

timestamp 必須是含時區的 RFC3339；可傳 `+08:00`、`Z` 或其他明確 offset。
無時區／無效日期回 422，與 dummy 的 state 同時使用也回 422。
URL 的加號必須編碼為 `%2B`，前端可用 URLSearchParams；curl 範例：

```sh
curl 'http://127.0.0.1:9999/api/graph/snapshots?limit=100'
curl --get 'http://127.0.0.1:9999/api/graph' \
  --data-urlencode 'timestamp=2026-09-12T13:09:58+08:00'
```

不帶 timestamp 仍回最新 live graph；帶 timestamp 只查歷史保存資料，即使 live 更新得更晚。
前端進入歷史模式後用清單的 at 查 graph，回到即時模式就移除 timestamp。
本次提供歷史 API，尚未修改 console 的時間軸操作。

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

## API 與執行限制

必須使用 **單一 uvicorn worker**。多個 instance 各自設定不同 snapshot_path，
不能只換 port 就共寫 checkpoint。同步檔案 I/O 適合此 demo 規模。
copytruncate 在兩次輪詢間縮短又長過原 offset 不保證偵測，建議 rename 輪替。

`GET /api/graph?state=normal|problem` 保留明確 dummy 預覽，不修改真實 snapshot；
未提供 state 才回傳 monitor graph。其他前端 mock API 仍由 `NIGHTWATCH_MOCK_DATA` 控制。

`POST /api/logs` 接受 `{"logs": [...]}`，每批 1–50 筆，全部通過
`console/schema-draft/log.schema.json` 格式驗證才接收，回傳 accepted／duplicates。
成功代表 checkpoint 已寫入；超過去重容量的舊事件可能再次接受。

`GET /events` 先送連線註解，新 log 送 event: log，閒置每 2 秒 ping。
Ping data 為 `{"server_now":"<RFC3339 UTC 時間>"}`，代表送出心跳的時間，
僅表示串流連線活動，不表示 graph 有新觀測；graph 的 `at` 仍使用台灣時間。
每個訂閱者最多 256 筆，落後會斷線；無 SSE id、歷史回放或補送。
API 無身分驗證，預設僅綁定 localhost。文件：`http://127.0.0.1:9999/docs`。
