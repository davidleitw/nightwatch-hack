# Guard Room 啟動與設定

這個目錄放設定與啟動腳本；真正的 HTTP server 在 [control/server/](../control/server/README.md)。

```sh
bash guardroom/restart.sh
```

預設監聽 `127.0.0.1:8001`。腳本先安裝鎖定依賴並驗證設定，再停止它先前啟動的同一個 server。PID／log 在 `.run/`；不會停止無法辨識歸屬的程序。

`PORT` 可改埠；同時設定 `NIGHTWATCH_GRAPH_URL=http://127.0.0.1:<port>/api/graph`，調查才會讀到正確來源。`GUARDROOM_CONFIG` 可指定其他設定。

## 資料從哪裡來

`shop-web.config.json` 指向 `control/tmp/monitor.jsonl`。server 每秒讀完整新行，按設定的 monitor_id 映射到 node，原子保存 checkpoint；預設每 5 秒另存一張快照，保留 900 秒。

目前設定是 `shop.health` → `shop-health`、`shop.products` → `shop-products`、`shop.db.query` → `shop-db`。**現行店面沒有 `shop.db.query` 的發送點，且 `shop.order.health` 未列入設定。** 這份拓樸尚未對齊拆分後的 catalog／cart／order，詳見 [盤點](../INTEGRATION.md)。

節點 traffic、errors、p95 由窗口內完成的 monitor 呼叫計算；saturation 沒有來源，回 `null`。`alive` 只表示窗口內有事件，並非 health probe。邊來自設定，量測為 `null` 且 observed=false。

HTTP 接收使用 `POST /api/logs`，資料型別以 `control/server/logs.py` 為準；不是 OTLP receiver。資料格式檢查和去重失敗會明確回報。
