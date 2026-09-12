# console(agent 前端):這個部份是什麼

> **2026-09-11:demo 用的不是這個。** 台上跑的是隔壁的 `console-v2/`(從 `poc/web/` 搬過來的那套,卡片頁 → 舞台頁左邊服務圖、右邊 agent 排查),起法見 repo 根的 `RUNBOOK.md` §1.2b。這份 `console/` 還在、還能跑,版面不同(服務圖在最底下),兩個可以同時起。
> 下面兩件事兩個前端都一樣:**報告頁是死的**(`/api/incidents/{id}/report` 與 `/timeline` control 沒實作,回 404),**對話分頁也是死的**(`POST /api/chat/messages` 回 404)。`console-v2` 已經把這兩個入口拿掉,`console` 還留著。稽核項數是動態的(現在六項),不要寫死五項。

一個 React 單頁,是評審與講者看 NightWatch 的三個畫面:**故障卡頁**(評審按一張卡把店面弄壞)、**舞台頁**(講者看 agent 在查什麼、按批准)、**報告頁**(結案後把 AI 標的時間軸、量測、真相放在一起,可以拖時間軸回放)。它**只顯示、不計算**:健康、趨勢、起點、差幾秒,全部由後端(control)算好給它。

## 在整體裡的位置

```
瀏覽器 ──GET /api/state(一進來拉一份整體狀態)──▶ nightwatch-control :3000
       ◀──SSE /events(每 5 秒一張服務圖、事故每一步、故障進度)──┘
       ──POST /api/faults、/api/incidents/{id}/approve、/abort、/api/rounds──▶

沒有 control 的時候:
瀏覽器 ──?fixture=catalog_pool_leak&t=<秒>──▶ 讀 fixtures/<卡>/ 六個靜態 JSON,自己照時間播
```

三個頁面靠網址 `#view=cards|stage|report&incident=<id>` 切換,都是同一份資料的三種看法。

## 鄰居與契約(全部在 `../contracts/`)

| 檔 | 跟你的關係 |
|---|---|
| `API.md` | **你唯一的資料來源**:REST、SSE、state 投影、事故 read model、journal 事件、工具結果的形狀、錯誤碼。§9 是 fixture 格式 |
| `schemas/*.schema.json` + `examples/*.json` | 每種資料的機器可驗形狀;你的 TypeScript 型別照這個手寫 |
| `fixtures/catalog_pool_leak/` | 一輪真實回合的錄影(六個檔,1.4 MB)。你從第一段任務就用它開發,不用等後端 |
| `fixtures/catalog.json` | 五張卡的公開欄位(fixture 模式的卡片頁用) |
| `manifest.yaml` | 13 個節點與版面提示(`layout.row/col`)。**執行期不要讀這個檔**,同樣的資料 control 會放在 `/api/state.capabilities.nodes` 給你 |

契約有問題**不要自己改契約檔**,寫在回報裡,協調者改。

## 你的替身:假 control

`../stubs/mock-control.ts` 在 `127.0.0.1:3999` 假裝成 control:`/api/state`、SSE `/events`(照 fixture 的時間把事件播出來)、`/api/graph/history`、`/api/faults/catalog`、`/api/incidents/{id}/report|timeline|snapshots`、POST 一律 202。`RATE=10` 可以十倍速播。

```sh
cd hackathon && bun stubs/mock-control.ts                       # 假 control
cd hackathon && NIGHTWATCH_CONTROL_URL=http://127.0.0.1:3999 bun stubs/dev-server.ts   # 前端 dev server,把 /api 與 /events 轉給假 control
```

`dev-server.ts` 從 `console/dist/` 端靜態檔,所以改了程式要重 build(或你自己在 console 裡放一個 watch)。

## 技術限制

- bun 1.4 + React 19 + TypeScript,build 用 `Bun.build`(不用 vite / webpack)。產物固定三個檔:`dist/index.html`、`dist/app.js`、`dist/app.css`,再加 `dist/fixtures/`(從 `../contracts/fixtures/` 複製)。`bun run build` 要能離線跑。
- 允許的第三方套件:`react`、`react-dom`、`@xyflow/react`(畫服務圖,可以不用、自繪 SVG 也行)。其他先不要。
- **不能外連任何東西**:字體、圖示、CDN 都不行(現場沒網路);用系統字體。
- 投影機 1920×1080 也要在 1440×900 讀得到:固定摘要主句 28–32px、活動流主文 18–20px、服務圖節點名至少 16px。
- 所有 UI 文字 zh-TW;服務名、工具名、事件名、error_code 保留英文。
- 動態只留:新訊息短淡入、必要的倒數、進行中提示、回放播放;尊重 `prefers-reduced-motion`。
- 顏色只是輔助,所有狀態都要有文字。健康:ok 低飽和綠、warning 橘、failing 紅、unknown 灰;判定:suspect 藍框、ruled_out 灰斜線、origin 紅框加標籤。健康與判定**不能用同一套顏色**。
- 只讀 `hackathon/` 底下的檔案。這個 repo 其他目錄(`archive/poc/`、`poc-v3/`)當作不存在。
- 已知的坑:把全域 `fetch` 存成物件屬性再呼叫,Chrome 會丟 `Illegal invocation`(bun 測試不會);要包成箭頭函式。這種只有瀏覽器才會出現的問題,測試抓不到,所以每段的「瀏覽器開」驗收不能省。

## 怎麼驗收(實作的人自己做)

每段任務有「驗收」:build、起假 control、開特定網址、看到什麼。截圖存 `console/.shots/`。

## 測試政策(摘要,全文在 `../POLICY.md`)

只寫任務指定的測試,用 `bun test`。指定的是資料層(把 state / SSE 事件 / fixture 變成畫面要的資料)這種純函式;畫面不寫測試,用截圖驗收。

## 不做

- 不做登入、多使用者、深色模式切換(固定一種主題就好)。
- 前端不算健康、趨勢、起點差值、耗時;後端沒給的數字就顯示「—」。
- 不做手機版(評審用筆電或平板看卡片頁就好,寬度 ≥ 768 可讀即可)。

## 每段任務結束時的回報格式

用中文寫三段:**做了什麼**、**依據什麼驗證的**(指令、開了哪個網址、看到什麼、截圖路徑)、**沒有驗證的**。契約有疑問也寫在這裡。
