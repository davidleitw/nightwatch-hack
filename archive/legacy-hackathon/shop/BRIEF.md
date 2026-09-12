# 店面(shop):這個部份是什麼

一個自己寫的小購物網站,故意做成「可以被慢慢弄壞」的樣子。它是整個 demo 的被觀察對象:agent 後端(control)看著它的量測找故障,人批准後 control 把它修好。它**不知道 control 存在**,只有一組內部設定端點讓外面調整「旋鈕」。

## 在整體裡的位置

```
   合成顧客(shopper)──HTTP──▶ storefront ──▶ catalog ──▶ postgres
                                   │ ├──────▶ cart
                                   │ └──────▶ checkout ─┬─▶ cart / catalog
                                   │                    ├─▶ payment ──▶ (進程內模擬的)付款供應商 primary / secondary
                                   │                    ├─▶ shipping ─▶ 稽核磁碟(tmpfs 2 MiB)
                                   │                    └─▶ postgres(orders,狀態 paid)
                                   │                              │
                                   │                              ▼
                                   │                        fulfillment(背景工人撈 paid 改 fulfilled)
   八個服務全部 ──OTLP──▶ otel-collector ──▶ Prometheus / Jaeger / control(log)
   六個服務 ──monitor/v1alpha2──▶ Guard Room :8090(SHOP_MONITOR_URL 有設才送)
   control ──PUT /internal/config──▶ 任一服務(注入故障、修復)
```

Monitor 包在**業務 HTTP 路由**外層(`internal/httpapi/server.go`,`internal/observability/observability.go:169` 的 `MonitorServer`),所以送事件的是
`storefront` `catalog` `cart` `checkout` `payment` `shipping` 這六個;`shopper` 與 `fulfillment` 沒有業務路由,沒接。
`SHOP_MONITOR_URL` 空字串就完全不建 Monitor,跟 `OTEL_EXPORTER_OTLP_ENDPOINT` 同一條規則。

八個服務是**同一個 Go 執行檔的八個子命令**(`shop storefront`、`shop catalog`…),部署時八個容器共用一個映像檔。

## 鄰居與契約(全部在 `../contracts/`)

| 檔 | 跟你的關係 |
|---|---|
| `SHOP-INTERNAL.md` | **你要實作的東西的規格**:內部端點、旋鈕表、對外 API、服務之間的呼叫、資料表、合成顧客 |
| `METRICS.md` | **你要報出去的量測名字**:span 屬性、instrument 名、log 字串。名字錯一個字,control 那邊的服務圖就是空的 |
| `manifest.yaml` | 13 個節點的清單。你的 `service.name` 必須等於這裡的 node id |
| `cards.yaml` | 五張故障卡:哪顆旋鈕、怎麼變。你只要保證旋鈕表的行為對,曲線是 control 的事 |
| `API.md` §2 | 埠與環境變數 |

契約有問題(做不到、寫錯、缺東西)**不要自己改契約檔**,寫在你的回報裡,協調者改。

## 哪些一定要一樣、哪些隨你

契約定好之後,店面裡面怎麼寫是你的事。**一定要一樣**的只有外面看得到的幾層,也就是 H5 檢查點會逐項比對的東西:

| 一定要一樣 | 寫在哪 | 誰靠它 |
|---|---|---|
| 八個服務名(`service.name`)、13 個節點 id、8 條邊 | `manifest.yaml`、`METRICS.md` §2.1 | control 的服務圖、模型的靜態提示 |
| 每個服務的 `/healthz` `/readyz` `/internal/config`(PUT 是合併)、shipping 的 `/internal/volume/rotate`;容器內聽 `:8080` | `SHOP-INTERNAL.md` §1 | control 注入、修復、就緒檢查;compose |
| 旋鈕名、型別、出廠值、轉了會發生什麼(含 log 字串、span 名、錯誤碼) | `SHOP-INTERNAL.md` §2 | 五張卡、偵測規則、稽核、模型找根因 |
| 量測名字、屬性、單位;span 名與屬性;log 欄位 | `METRICS.md` §2 | control 照抄的 PromQL、假 stack |
| storefront 的 `/api/*` 形狀與 `shop_session` cookie | `SHOP-INTERNAL.md` §5 | demo 時手動下單、之後可能拆出去的合成顧客 |
| 合成顧客四種結果、整體 ≥ 1 單/秒 | `SHOP-INTERNAL.md` §7 | 偵測規則 R1 的來源 |
| 一個映像檔、靜態執行檔、`FROM scratch` 跑得起來、不外連 | `deploy/`(協調者) | compose;現場沒網路 |

**隨你**:語言與框架(建議 Go,理由見下)、套件、目錄結構、資料表長相、內部服務之間的路徑與 body(`SHOP-INTERNAL.md` §6 是建議值)、HTML 長相、快取、重試策略。任務檔裡提到的做法(pgxpool、`FOR UPDATE SKIP LOCKED`…)都只是「已知做得到的一種」,不是規定。

一個條件:**換掉建議值之前,先確認換了之後上表還成立。** 換連線池套件沒關係,但 `shop.db.pool.in_use` 這個 gauge 還是要有;把 orders 改成真的訊息佇列也行,但 `orders-queue` 節點、producer / consumer 兩種 span、`shop.queue.*` 量測還是要出現。拿不準就問協調者,不要猜。

為什麼建議 Go:協調者的 Dockerfile 假設「一個靜態執行檔、交叉編譯到 linux」,OTel 的 Go SDK trace、metric、log 三種訊號都齊。換語言的話,Dockerfile、映像檔、離線的套件快取由你自己負責,契約不變。

## 技術限制(不管用什麼都要守)

- 沒設 `OTEL_EXPORTER_OTLP_ENDPOINT` 時不送量測,但程式要正常跑(主機上單獨測一個服務時用)。
- 主機上測試時 8080 被別的程式佔了,用 `SHOP_LISTEN=:18081` 這種。
- 網頁與程式不能外連任何東西(現場沒網路);套件前一天就要抓好(`go mod download` 或 vendor)。
- 只讀 `hackathon/` 底下的檔案。這個 repo 其他目錄(`archive/poc/`、`poc-v3/`)當作不存在。**一個例外**:`internal/monitor` 與 `internal/wire` 是從 `poc-v3/internal/` 原樣複製過來的(只改 import 路徑),要改行為回 poc-v3 改再複製過來。
- 用 Go 的話:1.26,單一 module `nightwatch/shop`,`cmd/shop/` 一個 main,`internal/<service>/` 一個服務一個套件;第三方套件建議 `pgx/v5`(含 pgxpool)、`otelpgx`、`google/uuid`、`go.opentelemetry.io/otel` 全家。

## 怎麼驗收(實作的人自己做)

每一段任務都有「驗收」一節,是一串 `go build` / 起服務 / `curl` 的步驟和預期輸出。做完先自己跑一遍,再交給協調者。

## 測試政策(摘要,全文在 `../POLICY.md`)

只在任務指定的地方寫測試,別的地方不寫。指定的測試是給**合併的人**看的:證明契約邊界的行為對(例如 PUT 合併語意),以及證明主線行為對(例如結帳一路寫到 orders)。測試用 `go test ./...` 跑,不需要 Docker、不需要網路;需要 postgres 的邏輯用介面隔開,測試給假的。

## 不做

- 不做真的帳號、庫存、多幣別、真的金流。
- 不做瀏覽器端 JS(頁面自動刷新用 `<meta http-equiv="refresh">` 就好)。
- 不做 k8s、不做 Docker(compose、Dockerfile 是協調者的)。
- 不做多卡同時注入的邏輯(那是 control 的)。

## 每段任務結束時的回報格式

用中文寫三段:**做了什麼**(檔案與行為)、**依據什麼驗證的**(哪些指令跑過、輸出是什麼)、**沒有驗證的**(需要 Docker / 網路 / 其他服務才能驗的)。契約有疑問也寫在這裡。
