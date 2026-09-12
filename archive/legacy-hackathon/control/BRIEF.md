# control(agent 後端):這個部份是什麼

一個 Go 程式,是整個 demo 的大腦。它每 5 秒去 Prometheus 把店面的量測組成一張「服務圖快照」(13 個節點 × 五個量),存進 15 分鐘的環形緩衝;用三條規則盯著,異常連續三張就開一件事故;開事故時把偵測前的快照釘住,叫模型(gpt-5.6-luna,OpenAI Responses 介面)用九個唯讀工具查根因(七個看指標與 trace,兩個看 Guard Room 的錯誤日誌);模型交出「根因 + 時間軸」後,Go 做六項稽核,過了才從 manifest 查出修復動作、等人批准、執行、等系統穩、量兩個 20 秒驗證窗、寫結案報告。它同時也是**注入故障的人**:照故障卡的曲線每 2 秒對店面 PUT 一次旋鈕。前端頁面**沒有**嵌進它的執行檔(那段沒做):control 是 API-only,`/` 回一頁「前端未 build」(`internal/server/server.go:481`),前端由 `stubs/dev-server.ts` 另外端。

## 在整體裡的位置

```
Prometheus ◀──每 5 秒一批 PromQL──┐
Jaeger     ◀──模型要 trace 時才問──┤
店面各服務 ◀──PUT /internal/config──┤ (注入故障 / 執行修復)
Guard Room ◀──啟動畫 8 條邊;排查時 ──┤ (選配:NIGHTWATCH_GUARDROOM_URL 有設才有)
           ◀──list_errors / get_node_errors
                                   │
                            nightwatch-control ──:3000──▶ 前端(GET /api/state、SSE /events、POST 批准…)
                                   ▲
otel-collector ──log(OTLP JSON)──▶ :3001 /internal/otlp/v1/logs
OpenAI ◀──Responses API,工具迴圈──┘
```

## 鄰居與契約(全部在 `../contracts/`)

| 檔 | 跟你的關係 |
|---|---|
| `API.md` | **你對前端開的所有東西**:REST、SSE、錯誤碼、state 投影、事故 read model、工具契約、基線與偵測規則的定義 |
| `schemas/*.schema.json` + `examples/*.json` | API 回應的機器可驗版本。`bun ../contracts/check-live.ts http://127.0.0.1:3300` 會打你的端點逐一驗 |
| `METRICS.md` | **你對 Prometheus / Jaeger 的查詢句**,照抄;`stubs/fake-stack.ts` 只認這些句型 |
| `SHOP-INTERNAL.md` | 你對店面下指令的方式:PUT 合併語意、旋鈕表、曲線公式、動作步驟 |
| `manifest.yaml` | 13 節點、8 邊、8 個健康檢查、機制家族、3 個動作。你**唯讀**,啟動時載入並驗證;接了 Guard Room 的話,那 8 條 `calls` 邊要在啟動時 `PUT /relationships/calls/{from}/{to}` 畫進去(`internal/server/server.go:255-275`,失敗會 30 秒重試) |
| `AGENT.md` | 排查迴圈、九個工具、六項稽核、驗證謂詞與結局。**只有你要讀** |
| `cards.yaml` | 五張故障卡。你唯讀 |
| `fixtures/catalog_pool_leak/` | 一輪真實回合的錄影;你結案時要能錄出同樣六個檔 |

契約有問題**不要自己改契約檔**,寫在回報裡,協調者改。

## 你的替身:主機假 stack

沒有 Docker、沒有真店面的時候,`../stubs/fake-stack.ts` 在主機上假裝成 Prometheus(`127.0.0.1:19090`)、Jaeger(`:19686`)、店面(`:19080`,路徑 `/svc/<service>/internal/config`),還會往 `http://127.0.0.1:${NIGHTWATCH_LOG_SINK_PORT}/internal/otlp/v1/logs` 送假 log。它只認 `METRICS.md` 裡的查詢句,認不得就回 400。你從第一段任務開始就能用它跑通整輪。

```sh
cd hackathon && NIGHTWATCH_LOG_SINK_PORT=3301 bun stubs/fake-stack.ts --speed 1 --seed 7
```

## 技術限制

- Go 1.26,單一 module(建議 `module nightwatch/control`),`cmd/nightwatch-control/` 一個 main,`internal/<套件>/`。
- 允許的第三方套件:`gopkg.in/yaml.v3`、`github.com/google/uuid`。其他的(含 OTel SDK)先不要;HTTP 用標準庫。
- 兩個埠:`NIGHTWATCH_CONTROL_ADDR`(公開,預設 `:3000`)、`NIGHTWATCH_CONTROL_INTERNAL_ADDR`(內部,預設 `:3001`)。主機開發用 `127.0.0.1:3300` / `:3301`。
- 交叉編譯要能 `CGO_ENABLED=0 GOOS=linux go build`;容器 `FROM scratch`(打 OpenAI 的憑證由部署那邊放進去)。
- 前端不嵌:`/` 回一頁純文字「前端未 build」就好,前端由 `stubs/dev-server.ts` 端 `console/dist` 或 `console-v2/dist`。(原本計畫用 `go:embed`,沒做。)
- Guard Room 是選配:`NIGHTWATCH_GUARDROOM_URL` 有設才掛 `list_errors` / `get_node_errors`、才畫邊、`error_log_cited` 才會判;空字串就整個退回七工具五稽核的樣子。`NIGHTWATCH_GUARDROOM_AUDIT_STRICT=1` 才讓 `error_log_cited` 擋提案。
- 模型金鑰只從環境變數讀,不寫進任何檔案、不印出來、錯誤訊息要遮掉。
- 只讀 `hackathon/` 底下的檔案。這個 repo 其他目錄(`archive/poc/`、`poc-v3/`)當作不存在。Guard Room 對你只是一個 HTTP 位址,它的程式在 `poc-v3/cmd/guardroom`,不歸你改。

## 怎麼驗收(實作的人自己做)

每段任務有「驗收」:起假 stack、起 control、`curl`、跑 `check-live.ts`。做完先自己跑一遍。

## 測試政策(摘要,全文在 `../POLICY.md`)

只寫任務指定的測試。指定的是給**合併的人**看的:純函式(曲線、基線、分類、稽核)對固定輸入的結果、契約邊界(端點回應形狀、冪等、409)。不寫需要真 Prometheus 或真模型的測試。

## 不做

- 不做決定論的假排查(模型不可用就明說,事故以 `unresolved` 結案)。
- 不做自動批准。
- 不做多事故並行、多卡同時注入。
- 不做 Loki / OpenSearch。

## 每段任務結束時的回報格式

用中文寫三段:**做了什麼**、**依據什麼驗證的**(指令與輸出)、**沒有驗證的**。契約有疑問也寫在這裡。
