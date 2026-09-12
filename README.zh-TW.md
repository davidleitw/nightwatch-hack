<p align="center">
  <img src="docs/readme/nightwatch-hero.png" alt="NightWatch：深夜中的瞭望塔照亮相連的服務節點。" width="100%">
</p>

<h1 align="center">NightWatch</h1>

<p align="center">
  <strong>讓異常有跡可循，讓調查有據可查。</strong>
</p>

<p align="center">
  <a href="README.md">English</a> · <strong>繁體中文</strong>
</p>

<p align="center">
  <a href="#快速開始">快速開始</a> ·
  <a href="#對接方式">對接方式</a> ·
  <a href="#未來工作">未來工作</a>
</p>

<p align="center">
  函式監測 · 服務圖 · AI 調查 · 證據保存<br>
  黑客松原型 · 本機示範 · 真實服務資料
</p>

---

## 服務出問題時，先知道該往哪裡看。

結帳變慢、錯誤增加、日誌散落在不同服務。NightWatch 的目標，是把這些訊號接成一條能追查的線：**看見異常、調閱證據、提出假設，留下可回看的調查報告。**

我們從一間真的能瀏覽商品、保存購物車與建立訂單的本機商店開始。Python Monitor 收集函式執行事件；Guard Room 將事件整理成服務圖與歷史快照；AI Agent 讀取圖、節點與近期日誌，交付結構化報告。Console 讓人追蹤調查進度，回看事件與模型對話。

**最終願景：從發現問題，一路走到經人批准、可驗證的修復。** 目前已實作監測、調查與報告；另可明確啟用本機演練故障解除與 health 查詢。通用修復、人工批准及可信的修復後驗證仍是未來工作。

## 值得打開它的四個理由

- **有真實場景。** 商店由 gateway、catalog、cart、order 組成，以 SQLite 保存資料，並提供結帳例外、資料庫寫入鎖與延遲三種真實故障演練。
- **調查有證據。** Agent 可讀取服務圖、歷史快照、節點詳情與近期日誌；報告包含發現、假設、限制與下一步，並驗證節點及證據 ID。
- **過程留得下來。** 調查、事件、模型對話與證據保存於 SQLite；後端提供報告與完整調查匯出 API。
- **不把未知塗成綠燈。** 缺少的量測保留為 `null`；模型或上游失敗會顯示錯誤，錄影與 mock 必須明確選用。

## 現階段能依賴什麼？

依 [2026-09-12 對接盤點](docs/readme/integration-status.md)，目前已串起「店面請求 → 部分 Monitor → Guard Room → 調查／報告」。自動偵測會在同一節點連續三次出現符合新鮮度條件的 `warning`／`failing` 時觸發調查；也可手動建立。

**監測尚未覆蓋拆分後的所有服務，因此店面的每張故障卡不保證會觸發調查。** Console 尚無批准修復入口；設定 `NIGHTWATCH_SHOP_URL` 後，Agent 可解除指定本機店面的演練故障，但這不等於業務恢復。看到調查報告或 mock 的 recovered 狀態，都不代表服務已被修好。

## 架構

```text
商店：React → FastAPI gateway → catalog / cart / order → SQLite
                                      |
                            僅已加入監測的函式
                                      v
                               Python Monitor
                                      |
                         共享 JSONL 或 HTTP 日誌 sink
                                      v
                            Guard Room HTTP API
                           服務圖／日誌／快照
                                      |
                           手動或自動偵測
                                      v
                               AI 調查
                                      |
                         報告／證據／SQLite
                                      |
                              HTTP + SSE 更新
                                      v
                                   Console
```

商店預設透過共享 JSONL 接入；HTTP sink 是其他 Python 服務可選的接法。圖中的監測範圍仍有限，詳見 [Guard Room 設定說明](guardroom/README.md)。

## 快速開始

以下命令從 repository 根目錄執行。需要 Git、Docker + Compose、GNU Make、Python 與 `uv`；各 Python 元件版本依自己的專案設定，Monitor 最低為 Python 3.11，商店容器使用 Python 3.12。Console 使用標準函式庫，不需 npm 安裝。

首次依賴安裝、映像下載與 Docker build 可能需要網路。預先備妥依賴與映像後，本機服務與明確選取的錄影可離線使用；**真實 AI 調查需要可達的模型端點與金鑰**，自動偵測也可能觸發模型呼叫。

```sh
git clone https://github.com/davidleitw/nightwatch-hack.git
cd nightwatch-hack

# 建置並啟動商店的五個容器。
make -C shop-web up

# 啟動 Guard Room，安裝鎖定依賴並驗證設定。
bash guardroom/restart.sh

# 建置 Console，並在此終端提供 production 建置產物。
python3 console/build.py
python3 console/serve.py --port 4173 --control-url http://127.0.0.1:8001
```

| 入口 | URL | 用途 |
| --- | --- | --- |
| 商店 | http://127.0.0.1:8080 | 商品、購物袋與示範訂單 |
| 故障演練 | http://127.0.0.1:8080/#/events | 三張作用於 order 的故障卡 |
| Console | http://127.0.0.1:4173 | 即時調查工作區 |
| Guard Room API | http://127.0.0.1:8001/docs | API 文件 |
| Storefront API | http://127.0.0.1:8000/docs | Gateway API 文件 |

在另一個終端確認服務回應：

```sh
curl --fail-with-body http://127.0.0.1:8080/api/health
curl --fail-with-body http://127.0.0.1:8001/health
curl --fail-with-body http://127.0.0.1:8001/api/graph
```

瀏覽商品以產生被監測的請求；空的觀測窗口可能顯示 unknown。商店沒有金流或物流。Guard Room 使用 **單一 worker**；更換埠號時也要調整 `NIGHTWATCH_GRAPH_URL`。

### 啟用 AI 調查

啟動 Guard Room **之前**，在同一個 shell 設定 `NIGHTWATCH_LLM_API_KEY` 或 `OPENAI_API_KEY`，前者優先。可用 `NIGHTWATCH_LLM_ENDPOINT` 與 `NIGHTWATCH_LLM_MODEL` 選擇部署所需端點及模型；預設值與 CLI 選項見 [Agent 說明](control/README.md)。HTTP server 只讀程序環境，不自行載入 `.env`。

只想先看介面？建置 Console 後，在另一埠明確開啟離線 mock；此模式不能建立真實調查。

```sh
python3 console/serve.py --port 4174 --mock
```

## 對接方式

### 1. 接入服務訊號

Python 服務可用 `@monitor(MonitorConfig(...))` 包住要觀測的函式，再選 JSONL 或 `GuardRoomSink` 背景批次傳送。HTTP 日誌入口為 **`POST /api/logs`**，格式為 `nightwatch.log.v1`，不是 OTLP。

先在 Guard Room 設定中建立 **`monitor_id` → node 對應**；未知 monitor 的日誌可被接收，但不會自動建立圖節點。生命週期與 sink 關閉範例見 [Monitor 接入指南](control/monitor/README.md)，現行 log schema 見 [log.schema.json](console/schema-draft/log.schema.json)。

### 2. 對接調查 API

| 方法 | Endpoint | 用途 |
| --- | --- | --- |
| GET | `/api/graph` | 最新圖；`timestamp` 查歷史 |
| GET | `/api/graph/snapshots` | 快照索引 |
| GET | `/api/debug/logs` | 最近保留日誌 |
| POST | `/api/investigations` | 建立調查 |
| GET | `/api/investigations/state` | 工作區狀態 |
| GET | `/api/investigations/stream` | SSE 狀態及調查更新 |
| GET | `/api/investigations/{id}/report` | 結構化結案報告 |
| GET | `/api/investigations/{id}/export` | 調查、證據、對話與用量 |
| GET | `/events` | 即時 log 與相容事件串流 |

真實調查範例，需要上方的模型設定：

```sh
curl --fail-with-body http://127.0.0.1:8001/api/investigations \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"readme-manual-001","trigger":{"source":"manual","reason":"Inspect current service anomalies"}}'
```

重送相同 `request_id` 與內容會取回同一調查；新調查請換 ID，重試則保留原 ID 及內容。同時只執行一件調查。報告尚未結案回 `409`，未知 ID 回 `404`。SSE 支援 `after` 或 `Last-Event-ID` 續傳，完整介面見 [HTTP API 說明](control/server/README.md)。

### 3. 對接商店

外部 client 使用 gateway `:8000`，或 Nginx 的 `:8080/api/`；catalog、cart、order 僅在 Compose 內部開放。商品、購物車、checkout 與 `Idempotency-Key` 的完整規則見 [商店 API 契約](shop-web/docs/API.md)。

故障卡使用商店的 **`GET/POST/DELETE /api/demo-faults`**。設定 `NIGHTWATCH_SHOP_URL` 後，Agent 可透過 `get_demo_faults`、`deactivate_demo_fault`、`check_shop_health` 查詢及解除本機演練故障，詳見 [演練修復邊界](control/SYSTEM_DESIGN.md)。舊 control `/api/faults*` 及批准操作在 live 模式仍回 `503`，兩套端點不可互換。

## 未來工作

以下是依現行對接缺口提出的工作順序，並非已完成的能力或交付時程。

| 優先 | 下一步 | 完成後的價值 |
| --- | --- | --- |
| 1 | 對齊 catalog／cart／order 的 Monitor 與圖設定 | 故障可對應到實際受影響服務 |
| 2 | 完善演練故障控制的並行操作與生命週期 | 補足已接通的本機解除工具之操作限制 |
| 3 | 人工批准、修復執行、修復後觀測窗口 | 用證據確認修復成效 |
| 4 | 補齊基線、指標、trace 與獨立 health probe | 讓調查取得更完整的觀測資料 |
| 5 | 全歷史日誌查詢、完整實驗稽核與 Console 匯出入口 | 更完整地回看與分享事故 |

想一起做？先從 [對接盤點](docs/readme/integration-status.md) 挑一個缺口，確認對應介面，再提出範圍清楚的 issue 或 PR。現行 `contracts/` 的 schema 與錄影仍有程式依賴；保留它們不代表其中的舊功能已上線。

## 探索專案

| 位置 | 內容 |
| --- | --- |
| [shop-web/](shop-web/README.md) | React 商店、FastAPI gateway 與三個業務服務 |
| [control/monitor/](control/monitor/README.md) | Python 函式監測、JSONL 與 HTTP sink |
| [control/](control/README.md) | AI 調查工具、模型設定與 CLI |
| [control/server/](control/server/README.md) | Guard Room HTTP API、SQLite、SSE |
| [guardroom/](guardroom/README.md) | 本機啟動與監測映射設定 |
| [console/](console/README.md) | 真實調查工作區與明確選用的錄影 |
| [對接狀態](docs/readme/integration-status.md) | 真實對接、mock 與限制的詳細盤點 |
| [INTEGRATION.md](INTEGRATION.md) | 主線保留的原始盤點紀錄 |
| `contracts/schemas/`、`contracts/fixtures/` | 程式仍載入的 schema 與離線錄影；舊架構說明已移除 |
| `gate/` | PR 與 harness 工具，獨立於網站執行流程 |

`gate/` 及根目錄的 `task.sh`、`run-task.sh`、`setup.sh`、`hackathon.conf` 是既有協作工具，並非服務啟動入口；舊編號任務資料已移除。

<p align="center"><strong>從看見異常，到理解原因。</strong></p>
