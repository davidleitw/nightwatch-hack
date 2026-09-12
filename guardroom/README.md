# Guard Room：config → monitor log → graph snapshot

以 `shop-web/backend/app` 為範例，單一程序、檔案儲存。API 回傳符合
`console/schema-draft/graph.schema.json` 的資料實例，不改寫 schema 定義。

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
   全失敗=failing、部分失敗=warning、全成功=ok、無完成資料=unknown。
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
首次啟動从 log 開頭讀，舊資料多時需數個週期追上。

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
