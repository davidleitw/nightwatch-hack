# 調查工作台與 Guard Room 接線

## 現在使用的 API

即時頁面已改用獨立的 `/api/investigations` 契約；不再等待舊 `/api/state` 的 capabilities/layout。錄影與明確啟動的 `--mock` 維持原有事故介面。請勿將它們當成真實調查整合證據。

依據使用者交付的介面，以及 `control/INVESTIGATION-FRONTEND.md`、`control/INVESTIGATION-SPEC.md`。已合併最新主線 `9ee5b7c`；本次前端接線沒有另行修改後端程式。

```text
瀏覽器 → console/serve.py → 後端 http://127.0.0.1:8001
```

瀏覽器使用同源 URL。`serve.py` 代理 GET 與指定的建立調查 POST；SSE 以 `read1` 逐段讀取並 flush，送 `X-Accel-Buffering: no`。部署的外層反向代理也必須關閉 SSE buffering。

| 介面 | 前端用途 |
| --- | --- |
| `GET /api/investigations/state` | 初始狀態與手動更新；active pointer 只由 state 決定 |
| `GET /api/investigations/stream` | `state`、`graph`、`investigation`、`ping`，使用 addEventListener |
| `POST /api/investigations` | 使用者按「開始調查」後才送出；202 取得 investigation_id |
| `GET /api/investigations?limit=20&before=…` | 全部結果的調查歷史與下一頁 |
| `GET /api/investigations/{id}` | 詳情、證據、用量及 terminal report |
| `GET /api/investigations/{id}/events?after=0&limit=100` | 刷新時恢復活動，依 next_after 讀完各頁 |
| `GET /api/investigations/{id}/context` | 使用者展開保存上下文，complete:false 明確標為部分對話 |
| `GET /events` | 獨立 Monitor `log` 與 `ping`；不從這條串流接 Agent tool call |

`investigation` SSE 裡再讀 `data.type`，支援 `investigation.started`、`tool.started`、`observation.recorded`、`tool.failed`、`investigation.finished`。工具依 `(investigation_id, payload.call_id)` 配對；成功證據讀 `payload.evidence`。目前僅有 `get_graph`，不假設已有 logs/traces 工具。

## 啟動與網址

從 repo 根目錄執行：

```sh
python3 console/build.py
python3 -B console/serve.py --port 4174 --control-url http://127.0.0.1:8001
```

開啟 <http://127.0.0.1:4174/?source=live#topology>；歷史為 <http://127.0.0.1:4174/?source=live#investigations>。完成驗證後 Ctrl+C 停止這次啟動的服務。

`--control-url` 填 base URL，不含 `/api/investigations`。也可使用既有 `NIGHTWATCH_CONTROL_URL`。不同電腦的 localhost 不互通，請由後端提供可達位址或 tunnel；不要只把對方的 localhost 貼過來。代理不轉送 Cookie 或 Authorization，若部署需要登入請先協調入口。

## 前端保存與恢復規則

- `stateWatermark` 與 `eventCursor` 分開。全新 SSE 的第一個 state 建立初始 cursor；重連 state 不推進事件 cursor，避免跳過尚未補送的事件。
- 重連使用 `?after=<eventCursor>`；依 `(investigation_id, seq)` 去重。歷史 REST 事件不改 SSE cursor。
- state 控制目前調查。舊 finished 只補入它自己的歷史，不清掉新 active；active 變 null 時清空目前調查內容，歷史報告與服務圖保留。
- graph 與調查分開保存，調查完成不修改健康。圖缺失及 graph_error 可見；保留舊圖時保留原始 seq/at，不以 ping 冒充新量測。
- **假設：新 graph 契約沒有 layout，前端按 node ID 穩定排序，每列最多四個節點。** 此為本機顯示位置，不寫回 API、不增減 node/edge，也不表示呼叫順序。
- 每次明確操作建立唯一 request_id；不確定的提交保存在 sessionStorage。使用者按重試或刷新後重試沿用同一 ID；409 顯示後端原因，不自動換 ID 重送。
- 調查失敗、interrupted、unresolved、budget_exhausted 都留在歷史。completed 表示 loop 結束，不表示服務恢復。缺少 agent_report 時仍呈現保存的結案摘要與 limitations。
- Monitor log 只保存本頁收到的資料，依 `(monitor_id, event_id)` 去重；刷新與斷線後不承諾補送。節點定位使用 refs.node_ids。

## 後端交付與實機驗收

後端需提供可由 console 主機連到的 base URL、部署 commit 與對應 `/docs`。graph 是否為真實 Monitor 資料須由部署負責人確認；調查 API 可用不代表 graph 已是真實量測。

```sh
curl -i --max-time 8 http://127.0.0.1:8001/api/investigations/state
curl -i --max-time 8 http://127.0.0.1:4174/__console/config
curl -i --max-time 8 http://127.0.0.1:4174/api/investigations/state
curl -N --max-time 12 http://127.0.0.1:4174/api/investigations/stream
curl -N --max-time 12 http://127.0.0.1:4174/events
```

SSE 長連線到期限的 curl exit code 28 本身不代表失敗；需檢查先前已送出的有效事件及到達時間。

1. 確認 config 的 mode=live、upstream 正確，頁面能顯示 graph 或明確 graph_error，歷史不因 graph 缺失而停止。
2. 按「開始調查」；檢查 POST body 的 request_id、manual trigger，202 的 ID 與後續 state 一致。刷新不能額外建立調查。
3. 檢查 investigation SSE 的開始／成功／失敗，確認同一調查的 call_id 配成一張工具卡，success evidence 來自 payload.evidence。
4. 調查 finished 後，在歷史打開保存報告及上下文；failed/interrupted 和不完整上下文都需可辨識。左圖健康不可自行變正常。
5. 刷新時透過 REST 補齊目前調查事件；斷線重連檢查 after 是否為最後實收的事件 cursor。state 先到、舊 finished 後到時，新 active 不可被清掉。
6. 409 顯示真實原因；提交逾時後重試保留相同 request_id。確認 Monitor log 串流不承擔 Agent 事件。

## 本次已驗證與限制

production build 與 ES module 語法已檢查；已在 Chrome 打開新的即時頁面，看到後端不可達的可見錯誤與狀態未知時停用的開始按鈕。同源 config 回 200 且 upstream 正確；建立調查與 state 代理回 502，未放行的 POST /api/logs 回 405。

本機 8001 的 `/docs`、`/openapi.json` 與 investigation state 均連線失敗。沒有啟動別的後端或假模型來代替；開始成功、工具實際配對、完成報告、刷新恢復、斷線補送及真實模型整合尚未端到端驗證。請以實際部署完成上列驗收後再宣稱串接成功。

正式 `contracts/API.md` 仍描述舊事故流程；本功能依使用者指定的新 investigation 契約實作，沒有修改正式契約或把新資料冒充舊 state.v2。
