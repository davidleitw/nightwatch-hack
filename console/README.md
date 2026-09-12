# NightWatch 前端：服務拓樸與調查工作台

即時頁面現在使用 `/api/investigations/state` 與 `/api/investigations/stream`，支援開始調查、工具配對、歷史報告及保存上下文。Monitor log 獨立訂閱 `/events`。接線、實機驗收與目前未驗證項目見 [Guard Room／調查 API 接線文件](GUARDROOM-INTEGRATION.md)。下方舊事故契約說明只適用錄影及明確的本機 mock。

原始碼在 `src/`，以原生瀏覽器 ES modules、CSS、SVG 實作。Python 標準函式庫將原始碼與契約錄影複製成可部署的 `dist/`，沒有第三方套件、CDN 或執行期建置依賴。`dist/` 是 Git 忽略的產物；請勿直接修改其中的檔案。

## 建置與啟動

從儲存庫根目錄執行：

```sh
python3 console/build.py
python3 -B console/serve.py --port 4173 --control-url http://127.0.0.1:8001
```

- 事故錄影：<http://127.0.0.1:4173/?source=recording#topology>
- 即時 API：<http://127.0.0.1:4173/?source=live#topology>
- 即時調查歷史：將 hash 改為 `#investigations`；錄影事故列表沿用 `#incidents`。

`serve.py` 只綁定 `127.0.0.1`，提供靜態產物，並代理 `/api/*` 與 `/events` 的 GET，以及唯一的寫入入口 `POST /api/investigations`。SSE 逐段 flush，不緩衝整條串流；正式反向代理也需關閉 buffering。結束時按 Ctrl+C。

## 功能與資料契約

### 歷史 graph 時間軸

即時工作台的服務圖下方提供歷史滑桿、上一張／下一張與「回到即時」。拖曳僅切換服務圖、節點詳情與量測來源；右側調查與 Monitor 紀錄維持目前狀態，不會啟動調查。歷史模式的開始按鈕標為「調查即時狀態」，不表示後端可以重新調查歷史時間。

- 使用 Guard Room 現行 `GET /api/graph/snapshots?limit=500`，依 `next_before_seq` 透過 `before_seq` 讀取後續頁；每 5 秒更新可用清單。實際範圍由清單決定，不寫死保留時間。
- `GET /api/graph?timestamp=<at>` 取得完整歷史 graph。使用 `URLSearchParams` 編碼時區加號；同時間只提供後端可查得的最大 seq。索引 seq 不必連續。
- 時間軸依時間比例顯示，取不晚於游標的快照；游標與實際圖的時間分開標示，均為台灣時間。快照間不插值，刻度只代表保存時間，不代表健康。
- 歷史模式固定時間軸範圍，背景持續接收即時 graph，按「回到即時」才切換。已過期快照會清除並提示；404、連線及格式錯誤可見，不退回錄影或假資料。
- 拖曳請求約每 150ms 最多一次，放開後完成最後選擇；取消過時請求並拒收與選取不一致的回應。載入期間清空舊圖，避免把舊量測標為新時間。沒有跨快照快取，每次切換向後端確認保留資料。
- 同一頁生命週期內，既有節點保留位置，新出現的節點接在後面；移除的節點不補造，只留下空位。支援方向鍵逐張操作。

這套操作只在真實 API 工作台啟用，錄影與明確 mock 沿用原操作。Guard Room 介面依使用者核准的歷史 graph 草稿接線；`contracts/API.md` 舊版 `?at=`／`not_found` 與現行 `?timestamp=`／`snapshot_not_found` 的差異仍待契約同步。

### Guard Room 即時拓樸接線

請先看 [Guard Room 接線與後端交接清單](GUARDROOM-INTEGRATION.md)。即時圖取 investigation state 的 `graph` 與 investigation stream 的 `graph`，不依賴舊 `/api/state`。新 API 未提供 layout，按節點 ID 穩定排列；健康與量測原樣呈現。尚未完成真實後端端到端驗證。

### 本機模擬與部署接線

不需要啟動 shop 或 control，也不需要下載套件。在專案根目錄執行：

```sh
python3 console/build.py
python3 -B console/serve.py --port 4174 --mock
```

開啟 <http://127.0.0.1:4174/?source=live&scenario=cycle#topology>。這是實際 HTTP 與 SSE 傳輸的**本機模擬 API**，不是事故錄影播放，也不是 shop 的即時量測。資料形狀與 13 個節點的位置取自既有 `state.initial.json`；數值由 `mock_control.py` 明確產生。只改變 `catalog` 的健康，不模擬事故、修復或 AI 結論。`unknown` 保持 `alive: true`，量測回 `null`，用來檢查缺資料不等於服務死亡。

「模擬情境」可選正常、警告、異常、無資料、每 10 秒輪換、HTTP 503。選擇只影響此頁網址的 `scenario`；不修改真實後端。`/events` 先送 `state`，每 5 秒送 `graph`，每 2 秒送 `ping`。此健康模擬沒有 incident journal，也不驗證事故 cursor 重播。

展開「後端接線」可查看目前來源、檢查 `/api/state` 格式及 SSE 的 `state`、`ping`／`graph`，並輸入 control API 網址產生啟動指令。輸入框**只產生指令**，不會直接修改正在執行的伺服器；檢查按鈕測的是目前來源。

接真實後端時先停止模擬服務，再執行：

```sh
python3 -B console/serve.py --port 4174 --control-url http://127.0.0.1:8001
```

亦可設定 `NIGHTWATCH_CONTROL_URL`；命令列 `--control-url` 優先。舊的 `--control-port` 仍可用，未指定 URL 時才生效。網址可用 HTTP(S) 與路徑前綴，不接受網址內帳密、query 或 fragment。填入的是**前端伺服器可達的 control base URL**，不是購物網站首頁，也不是完整 `/api/state` URL。

瀏覽器固定使用同源 API，由 `serve.py` 代理 GET 及建立調查 POST，不轉送登入 Cookie 或 Authorization。伺服器只綁 `127.0.0.1`；對外部署請使用現有反向代理，`/events` 與 `/api/investigations/stream` 必須關閉 buffering 並允許長連線。`/__console/config` 僅提供目前接線資訊，是 console 自己的端點。

`--mock` 是明確開關，不會因真實後端連不上而自動退回假資料。若同時設定 URL 與 `--mock`，會拒絕啟動；使用 mock 前請取消 `NIGHTWATCH_CONTROL_URL`。正式靜態產物仍是 `console/dist/`，建置現在包含 `connect.js`。

本次已建置並在 Chrome 開啟模擬頁，看到 13 節點、catalog 警告與模擬即時連線。其餘情境切換與接線按鈕交由使用者自行操作；真實 shop、遠端 HTTP(S) 代理與部署反向代理尚未驗證。

以下為舊錄影／本機 mock 的行為，即時調查 API 以本頁開頭連結的接線文件為準。

- 拓樸讀取 `GET /api/state` 的 `capabilities.nodes[].layout` 與 `graph_now`，節點數量與 ID 不寫死；只繪製資料中的實際連線。搜尋與健康篩選將無關節點變淡，不自行隱藏或重排其他服務。
- 健康使用 `status`，Agent 判定使用 `assessment`，節點主要量測取 `primary_axis`。點節點可查看五軸量測、後端趨勢與相鄰連線。缺量測顯示「—」，`observed:false` 連線使用虛線，不表示斷線。
- 事故列表讀取 `GET /api/incidents`，依偵測時間新到舊排列；搜尋 ID、故障卡與狀態，篩選處理中／已結束。點事故讀取 `GET /api/incidents/{id}`，顯示摘要、根因與證據；定位節點時顯示目前快照，不冒充歷史快照。
- 歷史列表 API 若回 404，清楚標示僅顯示 `state.incident`。其他 API 或格式錯誤會顯示錯誤，不自動換成錄影資料。
- 即時模式連接 SSE `state`、`graph`、`incident`、`run`；收到新事故事件時重新讀取完整投影，不自行推算事故狀態。心跳納入新鮮度計算，6 秒無事件顯示 stale、15 秒 disconnected；重連間隔 1／2／4／8 秒，帶既有事故 cursor。
- 只讀觀測，不提供批准、中止、注入故障或聊天。

## 錄影來源與人工檢查

建置時原樣複製 `contracts/fixtures/catalog_pool_leak/` 的四份檔案，不建立假事故。最早事件發生於 **2026-09-10 01:29:05 UTC**，記錄於 01:29:06；第一張快照在 01:29:06 UTC（台灣 09:29:06）。事故於 01:33:01 UTC 偵測，約 01:36:01 結案。

頁面錄影滑桿以事故偵測為 +0 秒；目前提供 0–180 秒，檔案原本也包含事故前 235 秒的快照。依錄影事件既有 `t` 重建事故投影，切回較早時間會重新建立狀態，不保留後續證據。這是舊版專案的 13 節點錄影，不是今天系統的即時狀態，也不是完整事故歷史庫。

1. +60 秒：13 節點、13 連線，事故「等待批准」。點 `catalog`，錯誤率 60%、P95 640 ms、飽和度 100%。
2. 搜尋 `payment` 符合 3 節點；清空搜尋並選「異常」符合 4 節點。
3. 事故列表只出現錄影中的 1 件事故；不符條件的搜尋／篩選有空狀態。
4. +0 秒事故「排查中」、+180 秒「已修復」；來回切換後清單與詳情同步。
5. 詳情可讀根因與證據，點服務可返回拓樸定位。

## 已知限制

- 真實 control、SSE 重連、換輪及歷史列表尚待接線驗證。錄影驗證不能代表真實後端整合通過。
- 錄影的 `hypothesis.concluded.changed_nodes` 使用舊值 `assessment: supported`，與正式節點契約的 `origin` 不同。因此錄影圖沿用 `snapshots.jsonl` 中合法的判定值，不把 `supported` 私自改名；根因文字仍可在事故詳情讀取。
- 尚未使用完整 JSON Schema validator；前端只檢查必要資料形狀、ID 對應與位置等邊界。
- 窄視窗下拓樸可捲動；縮放下限保留節點文字可讀性。

## AI 事故模板預覽

建置後開啟 <http://127.0.0.1:4173/incident-template.html>。原始碼為 `src/incident-template.html`，依 `message.txt` 的範例內容呈現 AI 結論、因果鏈、建議與可展開的日誌證據。日誌時間線可搜尋與分類；僅包含模板的七組日誌，不是完整原始日誌串流。這是獨立靜態預覽，未串接真實 AI 或事故 API。

## 調查工作台與事故模板

右側「Agent 用量」顯示事故累計 token、輸入／快取輸入／輸出拆分，以及 Prompt cache 命中率（快取輸入 ÷ 全部輸入）。取自 `incident.usage`，隨既有 SSE 事故事件重拉完整狀態；數值不重複累加。展開「統計口徑與計算方式」可查看公式、模型呼叫次數及耗時。缺值顯示「—」，非法值在用量區顯示錯誤；舊錄影没有事故累計用量，不以示意數字填補。資料設計與後端待確認事項見 [schema-draft/USAGE.md](schema-draft/USAGE.md)。

主頁改為左側 graph、右側調查面板，沿用事故模板的淺色、細分隔線與文字層級。右側保持 AI 結論，切換「調查事件」或「AI 事故報告」；報告呈現現有事故的起點、傳播路徑、修復提案與可展開證據。點右側服務名稱可定位左圖。窄於 761px 時上下排列。

錄影直接讀既有 incident journal，隨時間滑桿回放；Monitor／Agent／系統分開標示，可篩選。這份錄影沒有 Monitor 原始 log，Monitor 頁籤會顯示空狀態。即時頁面接收 SSE incident 與草案 nightwatch.log.v1 的 log，僅保留本頁收到的事件，不宣稱完整歷史；Monitor 的後端投影仍待正式契約同步與實機驗證。
