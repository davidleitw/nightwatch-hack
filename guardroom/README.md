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
7. **讀 graph**：`GET /api/graph` 回傳已提交 snapshot；
   `GET /events` 提供 live log SSE。本階段尚未串接 `/api/state`、SSE graph 或 console 畫面。

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
shop.products → shop.db.query
```

分別掛在 `health()`、`products()`、共用 `_query()`；
本次只讓 health 與商品列表的 SQL 走該 helper。沿用 monitor JSONL sink 與
Compose 的 `control/tmp/monitor.jsonl` 共用掛載，無需額外 HTTP bridge。
monitor 不需要填 node_ids，由 Guard Room config 映射。

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
P95 使用完成事件的函式耗時，不是 HTTP middleware 的 request duration，也不是 edge 延遲。

### Shop logger

Shop 啟動時將 monitor handler 掛到 `shop` logger。三個 monitor 的 `level="WARNING"`
只收執行期間的 WARNING／ERROR／CRITICAL logger 訊息；DEBUG／INFO 不進 monitor，
應用程式的 stdout 和 `/logs/app.log` 仍依原本 `LOG_LEVEL` 設定輸出。
Logger 本身先過濾掉的訊息無法由 monitor 補回，例如 `LOG_LEVEL=ERROR` 會略過 WARNING。
`started`／`finished`／`exception` 生命週期事件全部保留，不受此門檻影響。

函式內的 warning／已處理例外可作為調查資訊；巢狀呼叫的 log 歸屬最內層 monitor。
目前這三個函式沒有額外的業務 logger 呼叫，新增實際異常處理時可在函式內記錄。
未處理例外已由 monitor 自動捕捉，不需再記錄一次相同 exception。
啟停與 request middleware 的既有 log 不在 monitor invocation 內，仍只寫應用日誌。

### 啟動與觀察

從 repo 根目錄執行（需 uv、curl、lsof、Docker）：

```sh
./guardroom/restart.sh
./shop-web/restart.sh
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/products
# 等下一個一秒讀取週期
curl http://127.0.0.1:8001/api/graph
```

應有 shop-health、shop-products、shop-db 三個節點和兩條 edge。
首次啟動從 log 開頭讀，舊資料多時需數個週期追上。上述單次 curl 只驗證資料流，
不會累積到商品列表 latency warning 所需的 20 筆，也不保證耗時超過門檻。

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
預設 checkpoint：`guardroom/.run/shop-web.graph-state.json`。

| 欄位 | 用途 |
| --- | --- |
| version | checkpoint 版本 1 |
| snapshot | 符合 graph.schema.json 的 graph；亦為 GET /api/graph 回應 |
| recent_logs | 去重與恢復聚合的近期 console logs |
| file_cursor | log 路徑、device/inode、byte offset |

用 `jq '.snapshot' guardroom/.run/shop-web.graph-state.json` 匯出純 graph。
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
curl 'http://127.0.0.1:8001/api/graph/snapshots?limit=100'
curl --get 'http://127.0.0.1:8001/api/graph' \
  --data-urlencode 'timestamp=2026-09-12T13:09:58+08:00'
```

不帶 timestamp 仍回最新 live graph；帶 timestamp 只查歷史保存資料，即使 live 更新得更晚。
前端進入歷史模式後用清單的 at 查 graph，回到即時模式就移除 timestamp。
本次提供歷史 API，尚未修改 console 的時間軸操作。

## API 與執行限制

預設綁定 `127.0.0.1:8001`；`PORT=8002 ./guardroom/restart.sh` 可換 port。
PID、server log、snapshot 位於 `guardroom/.run/`。script 先驗證 config 再停止舊程序，
只停止自己啟動且命令符合的 PID；其他程式占用 port 時報錯。

必須使用 **單一 uvicorn worker**。多個 instance 各自設定不同 snapshot_path，
不能只換 port 就共寫 checkpoint。同步檔案 I/O 適合此 demo 規模。
copytruncate 在兩次輪詢間縮短又長過原 offset 不保證偵測，建議 rename 輪替。

`GET /api/graph?state=normal|problem` 保留明確 dummy 預覽，不修改真實 snapshot；
未提供 state 才回傳 monitor graph。其他前端 mock API 仍由 `NIGHTWATCH_MOCK_DATA` 控制。

`POST /api/logs` 接受 `{"logs": [...]}`，每批 1–50 筆，全部通過
`console/schema-draft/log.schema.json` 格式驗證才接收，回傳 accepted／duplicates。
成功代表 checkpoint 已寫入；超過去重容量的舊事件可能再次接受。

`GET /events` 先送連線註解，新 log 送 event: log，閒置每 2 秒 ping。
每個訂閱者最多 256 筆，落後會斷線；無 SSE id、歷史回放或補送。
API 無身分驗證，預設僅綁定 localhost。文件：`http://127.0.0.1:8001/docs`。
