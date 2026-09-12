# NightWatch 前端：服務拓樸與事故列表

原始碼在 `src/`，以原生瀏覽器 ES modules、CSS、SVG 實作。Python 標準函式庫將原始碼與契約錄影複製成可部署的 `dist/`，沒有第三方套件、CDN 或執行期建置依賴。`dist/` 是 Git 忽略的產物；請勿直接修改其中的檔案。

## 建置與啟動

從儲存庫根目錄執行：

```sh
python3 console/build.py
python3 console/serve.py --port 4173 --control-port 3300
```

- 事故錄影：<http://127.0.0.1:4173/?source=recording#topology>
- 即時 API：<http://127.0.0.1:4173/?source=live#topology>
- 事故列表：將 hash 改為 `#incidents`，或使用頁面上方導覽。

`serve.py` 只綁定 `127.0.0.1`，提供靜態產物，並把 `/api/*` 與 `/events` 的 GET 請求代理到同機的 control 埠；不代理寫入請求。正式部署可直接將 `dist/` 交由 control 或其他同源靜態伺服器提供。結束時按 Ctrl+C。

## 功能與資料契約

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

主頁改為左側 graph、右側調查面板，沿用事故模板的淺色、細分隔線與文字層級。右側保持 AI 結論，切換「調查事件」或「AI 事故報告」；報告呈現現有事故的起點、傳播路徑、修復提案與可展開證據。點右側服務名稱可定位左圖。窄於 761px 時上下排列。

錄影直接讀既有 incident journal，隨時間滑桿回放；Monitor／Agent／系統分開標示，可篩選。這份錄影沒有 Monitor 原始 log，Monitor 頁籤會顯示空狀態。即時頁面接收 SSE incident 與草案 nightwatch.log.v1 的 log，僅保留本頁收到的事件，不宣稱完整歷史；Monitor 的後端投影仍待正式契約同步與實機驗證。
