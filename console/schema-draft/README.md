# NightWatch hackathon：前後端 schema 交接

Guard Room 實際接線請看 [接線與後端交接清單](../GUARDROOM-INTEGRATION.md)：即時調查已改用 `/api/investigations` 的獨立 state、stream、歷史與報告。下方 state/event schema 保留舊事故契約，不拿來驗證新的 investigation envelope；graph 與 Monitor log 仍沿用既有格式。

新增：[Agent token 與 Prompt cache 命中率](USAGE.md)；`usage.schema.json` 約束既有事故 `usage` 欄位，`state.schema.json` 引用它。用量為事故累計快照，UI 不累加工具事件。

目前已確定：**左側以 8–12 個節點為版面目標，數量與服務名單不寫死；monitor log 收到就出現在右側，與 Agent 調查紀錄共用時間線。**

這份目錄是提案文件與 JSON Schema，尚未修改後端、正式契約或實際接線。以下四份就是本次必要範圍；不做動態分組或逐字思考串流。

## 先看這張清單

| 檔案 | 對應入口 | 內容 |
|---|---|---|
| [state.schema.json](state.schema.json) | `GET /api/state`、SSE `state` | 初始整體狀態、動態節點清單與 layout、服務圖、事故進度 |
| [graph.schema.json](graph.schema.json) | `GET /api/graph`、SSE `graph` | 節點、真實連線、健康、Agent 判定與量測 |
| [event.schema.json](event.schema.json) | SSE `incident` | Agent 查詢、工具結果、結論與既有事故事件 |
| [log.schema.json](log.schema.json) | **新增提案：SSE `log`** | monitor log 到達後立即送給前端，不等待 Agent 查詢 |

前面三份引用 `../../contracts/schemas/` 的既有格式，保留原本 required 欄位；log 是新定義的前端投影。交接請保留目錄結構，或由後端將相對 `$ref` 一起搬到正式契約位置。JSON Schema 的 `$schema` 網址只是標準識別，這套相對引用不需要連線下載資料。

## 節點名單由後端決定

8–12 個是畫面設計目標，JSON Schema 不以 `minItems`／`maxItems` 限制節點數，也不把服務 ID 寫成固定 enum。下列十個節點只是依現行故障卡挑出的建議示例，不是必填名單。

| id | 畫面用途 |
|---|---|
| `frontend-proxy` | 入口 |
| `frontend` | 商店前端 |
| `product-catalog` | 商品查詢 |
| `cart` | 購物車與假線索 |
| `checkout` | 結帳與修復目標 |
| `payment` | 付款故障 |
| `recommendation` | 推薦快取故障 |
| `email` | 確認信故障 |
| `kafka` | 訂單積壓 |
| `fraud-detection` | 消費者與風控延遲 |

上述示例涵蓋現有五張故障卡的根因、主要傳播與假線索；ID 取自 manifest。後端可依本次 demo 調整名單，縮圖不代表刪除其他實際服務或 Agent 偵測資料來源。

`state.capabilities.nodes` 與 `graph.nodes` 必須使用相同的 ID 集合，每個 ID 唯一；邊的兩端必須存在於這份清單。這些跨欄位關聯由後端驗證，JSON Schema 未完整表達。假設同一輪版面穩定；若後端變更名單或 layout，須送新的完整 `state` 讓前端同步。沒有量測時節點仍保留，數值使用正式契約允許的 `null`。圖上只畫 manifest 中兩端皆在名單內的真實邊，不將省略的中間路徑虛構成直接呼叫。

`status` 表示健康，`assessment` 表示 Agent 判定，兩者分開顯示。邊的 `observed:false` 不代表斷線。`fraud-detection → kafka` 的 `consumes` 表示風控消費 Kafka，不是訊息發送方向。

## Agent 事件

```text
tool.started → observation.recorded → hypothesis.concluded
  查什麼          查到什麼                  結論
```

- `tool.started.payload.note_zh`：Agent 對使用者說明的查詢目的；工具卡開始執行。
- `observation.recorded.payload.result`：工具結果；用相同 `call_id` 更新工具卡，搭配事件的中文摘要與 `evidence_ids`。
- `hypothesis.concluded.payload.hypothesis`：最終根因、信心與引用證據；後端稽核與是否修復仍取事故狀態。

`event.id` 防重複，`call_id` 配對開始與結果，`refs.node_ids` 定位左圖。每筆事件都有 `refs.node_ids` 與 `refs.evidence_ids`，無關聯時回空陣列。工具結果細部格式沿用 `contracts/API.md` §8，此草案沒有重寫全部工具結果 schema。

完整示例：[examples/agent-tool-started.json](examples/agent-tool-started.json)。示例是格式說明，不是真實 Agent 輸出。

## 即時 monitor log

```text
Monitor log → 後端接收並對應節點 → SSE log → 右側新增一列
```

Monitor 的原始輸入仍遵循它自己的契約。後端把收到的 log 轉成 `log.schema.json`，主動推到前端；這不是讓 monitor 直接呼叫前端，也不需包成一次 Agent 工具查詢。

| 欄位 | 必填 | 說明 |
|---|---|---|
| `schema_version` | 是 | 固定 `nightwatch.log.v1`，本次新增提案 |
| `event_id` | 是 | 原始 log 的穩定識別；重送不改 ID |
| `monitor_id` | 是 | 來源 monitor 的真實識別 |
| `occurred_at` | 是 | log 原本發生時間，UTC RFC3339；保留小數秒 |
| `level` | 是 | `TRACE / DEBUG / INFO / WARN / ERROR / FATAL` |
| `message` | 是 | log 文字，前端當純文字呈現 |
| `refs.node_ids` | 是 | 後端將 monitor 對應到服務 ID；無法對應時 `[]` |
| `instance_id`、`invocation_id`、`attributes` | 否 | 來源有提供時保留；未提供就省略，不造值 |

完整示例：[examples/log.json](examples/log.json)。`payment-monitor` 是示例來源名稱，實際值取 monitor 的識別。

同一條 `GET /events` SSE 連線新增以下訊息，訊息以空行結束：

```text
event: log
data: {"schema_version":"nightwatch.log.v1","event_id":"log-demo-001","monitor_id":"payment-monitor","occurred_at":"2026-09-12T08:00:05Z","level":"ERROR","message":"付款請求逾時","refs":{"node_ids":["payment"]}}

```

後端不等待 Agent 查詢、不等待每 5 秒的 graph 更新，也不限定已開事故才能送 log。建議驗收目標：正常連線下，後端收到合法 log 後 1 秒內開始推送；這是提案目標，尚未實測。

Log 依 `(monitor_id, event_id)` 去重；Agent 事件使用自己的 `event.id`，兩者不共用去重集合。右側依收到的順序追加，顯示原始發生時間；延遲到達的舊 log 不讓已顯示內容重新跳動，也不宣稱各來源有完全一致的時間順序。

Log 與 Agent 紀錄分別標示來源。點 log 用 `refs.node_ids` 定位左圖；圖外服務保留原 ID、顯示圖外來源，不由單筆 log 擅自新增圖上節點。無法對應時仍顯示 `monitor_id`，不能猜節點。單筆 ERROR 不直接覆寫節點健康。

## 連線與事故狀態

- 初始資料用 `state.schema.json`；`incident` 為 null 時仍接收 log。
- `graph` 每 5 秒、`ping` 每 2 秒，沿用既有契約；6 秒沒任何事件顯示 stale，15 秒 disconnected。來源健康另外依 graph 的 `sources` 呈現。
- `incident` 保留 `run_id`、`base_revision`、`revision` 與既有 cursor。版本不連續重拉 state；補送歷史事件不能回退最新投影。
- 新增 `log` 不設定 SSE `id:`，不改 incident revision，也不把 `event_id` 寫進 incident cursor。
- **假設：hackathon 第一版只保證連線期間收到的 log 即時呈現。** 不新增 log 歷史 API、落地保存或斷線補送；重連／重新整理後可能缺 log，畫面要說明這段限制，不宣稱歷史完整。
- 事故摘要、稽核、提案與驗證沿用正式 read model；批准沿用 `POST /api/incidents/{id}/approve` 的 `request_id`、`proposal_id`。
- 事故終止後，尚未拿到結果的工具卡顯示「調查已結束，未收到結果」。驗證只顯示進行中與已完成窗口，不新增精確倒數。

## 需要由後端同步的契約

1. Demo 節點清單以 8–12 個為目標，由後端提供：`capabilities.nodes`、`graph_now`、`GET /api/graph` 與 SSE `graph` 一致；內部完整偵測資料不可因輸出縮圖而被無意刪除。
2. 既有事件的 `refs.node_ids`、`refs.evidence_ids` 在此 demo 必填，允許空陣列。
3. 新增 SSE `log` 與 `nightwatch.log.v1`，需同步正式 API／schema；現有 `contracts/schemas/sse-line.schema.json` 的事件清單也尚未包含 `log`。monitor 到服務 ID 的 mapping 由後端維護。監控原始 log 接收端點與 bridge 不由這份前端 schema 取代。
4. 工具失敗／逾時的終止 payload 仍需後端補明確契約，不能將目前成功結果 schema 當成已涵蓋錯誤。

## 驗證範圍

本輪查核 JSON 語法、相對引用、動態節點規則與示例形狀；未安裝新套件，未使用完整 JSON Schema validator，未接真實 monitor 或 SSE 服務。後端實作後至少實跑：使用不同節點清單時前端可同步、有／無事故都能收到 log、同一 log 重送不重複、點 log 定位正確節點、log 不干擾 incident 重連游標。

依據：`contracts/manifest.yaml`、`cards.yaml`、`API.md`、`oteldemo/ERROR-LOG.md` 與 `contracts/schemas/`。本目錄只編輯前端交接提案，正式契約未改動。
