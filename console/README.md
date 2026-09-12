# NightWatch 觀測介面

預設開啟真實調查工作區：讀取 `/api/investigations/state`、歷史調查與 SSE，透過 `POST /api/investigations` 建立調查。即時 monitor log 使用另一條 `/events` 連線。

## 建置與啟動

只重啟 Console（不需要 Docker），從任意目錄執行腳本：

```sh
bash console/restart.sh
```

以上路徑以 repo 根目錄為例。腳本先建置 `dist/`，再停止同一份 checkout 在指定 port 的 Console，並於背景啟動；不會停止其他程式。預設 `CONSOLE_PORT=4173`，沿用正在執行的 Console 上游，沒有舊服務時使用 `http://127.0.0.1:9999`。可用 `NIGHTWATCH_CONTROL_URL` 明確指定，例如：

```sh
NIGHTWATCH_CONTROL_URL=http://127.0.0.1:8001 bash console/restart.sh
```

PID 與日誌位於 `console/.codex/run/console-<port>.pid`、`console-<port>.log`。腳本檢查首頁與調查 API；上游檢查失敗會回傳非零狀態並保留 Console，方便查看錯誤。Shop 與 Guard Room 需另外啟動。

從 repo 根目錄執行，不需要第三方套件：

```sh
python3 console/build.py
python3 console/serve.py --port 4173 --control-url http://127.0.0.1:9999
```

開啟 `http://127.0.0.1:4173/`。server 只供應 `dist/` 建置產物，代理 `/api/*` GET、`/events` 與建立調查 POST。其他 POST 回 405；上游失敗顯示錯誤，不切換成 mock。

`NIGHTWATCH_CONTROL_URL` 可設定上游；未設 URL 時使用 `--control-port`（預設 9999）。

## 報告與分頁連線

調查事件支援 `report.submitted`，與 `submit_report` 工具呼叫配對並顯示「已提交報告」。結案報告優先讀取 `investigation_report`，相容舊 `agent_report`。

背景分頁會暫停調查與 monitor log 兩條 SSE，避免多分頁占滿 HTTP 連線而讓歷史／事件讀取逾時。回到前景時先刷新資料，再由既有 cursor 恢復調查串流；monitor log 不補送背景期間的缺漏。更新後請重新整理已開啟的舊分頁。

## 歷史 graph 時間軸

即時工作台的服務圖下方提供歷史滑桿、上一張／下一張與「回到即時」。拖曳僅切換服務圖、節點詳情與量測來源；右側調查與 Monitor 紀錄維持目前狀態，不會啟動調查。歷史模式的開始按鈕標為「調查即時狀態」，不表示後端可以重新調查歷史時間。

- 使用 Guard Room 現行 `GET /api/graph/snapshots?limit=500`，依 `next_before_seq` 透過 `before_seq` 讀取後續頁；每 5 秒更新可用清單。實際範圍由清單決定，不寫死保留時間。
- `GET /api/graph?timestamp=<at>` 取得完整歷史 graph。使用 `URLSearchParams` 編碼時區加號；同時間只提供後端可查得的最大 seq。索引 seq 不必連續。
- 時間軸依時間比例顯示，取不晚於游標的快照；游標與實際圖的時間分開標示，均為台灣時間。快照間不插值，刻度只代表保存時間，不代表健康。
- 歷史模式固定時間軸範圍，背景持續接收即時 graph，按「回到即時」才切換。已過期快照會清除並提示；404、連線及格式錯誤可見，不退回錄影或假資料。
- 拖曳請求約每 150ms 最多一次，放開後完成最後選擇；取消過時請求並拒收與選取不一致的回應。載入期間保留目前的圖與實際快照時間，另標示正在載入的目標；成功後才切換，失敗或過期仍清除並顯示錯誤。沒有跨快照快取，每次切換向後端確認保留資料。
- 快照刷新保留既有節點、連線與時間軸刻度，只更新改變的內容；節點焦點與展開的原始資料不會因刷新而重置。
- 同一頁生命週期內，既有節點保留位置，新出現的節點接在後面；移除的節點不補造，只留下空位。支援方向鍵逐張操作。
- 卡片優先顯示後端指定的 `primary_axis`。未指定時，服務優先顯示流量、資料庫優先顯示 P95、佇列與儲存空間優先顯示飽和度，再從其他已有數值的量測選擇；全無數值顯示「尚無量測」，指定量測缺值顯示「未回報」，不把缺值當成 0。

這套操作只在真實 API 工作台啟用，錄影與明確 mock 沿用原操作。Guard Room 介面依使用者核准的歷史 graph 草稿接線；`contracts/API.md` 舊版 `?at=`／`not_found` 與現行 `?timestamp=`／`snapshot_not_found` 的差異仍待契約同步。

## 明確啟用的示範

```sh
python3 console/serve.py --port 4174 --mock
```

`--mock` 使用 `mock_control.py` 的本機模擬 API，不支援建立真實調查；不可與 `--control-url` 或 `NIGHTWATCH_CONTROL_URL` 同時使用。

任一靜態服務的 `/?source=recording` 會切到舊事故錄影。`app.js`、`data.js`、`incident-template.html` 與 `contracts/fixtures/catalog_pool_leak/` 仍支援這兩種明確選取的示範，因此保留。

control 自己的 `NIGHTWATCH_MOCK_DATA=1` 是另一個獨立開關；它不等於 console 的 `--mock`。預設調查工作區不會因 control 的舊 state API 啟用 mock 就自動切換。

## 目前限制

畫面沒有故障注入、批准修復、換輪或對話入口。獨立報告頁讀調查 detail／events／context；`snapshots`、`export` 專用端點尚無獨立匯出操作。圖上監測範圍與未對接項目見 [盤點](../INTEGRATION.md)。

## 調查對話與報告

右側使用對話紀錄：Agent 文字、可展開的公開推理摘要（API 未提供文字時顯示摘要不可用）、工具狀態／參數／結果與獨立報告卡。置頂用量列和 Usage 頁共用欄位對應：`cache_read_tokens` 優先於 `cached_tokens`、`requests` 優先於 `calls`，總 token 為輸入加輸出，快取命中率為快取讀取除以輸入。未知顯示 `—`，異常仍提示。

`#investigations` 是歷史清單，`#investigations/<id>` 是獨立報告，`#investigations/<id>/chat` 是保存的對話；可相互切換。結束後右側保留最近一筆調查。報告先讀已收到的提交事件，保存的 detail 到達後補上證據與結案資訊。

文字與摘要依模型每次實際回覆更新；沒有對話輸入或逐 token 推播。新的 `agent` SSE 事件使用同一條調查連線。讀取歷史時使用 `include_messages=1`，舊後端拒絕新參數時退回原事件 API 並標示沒有文字回放。未保存的舊訊息不會憑空補出。

## 即時畫面更新與顯示範圍

Shop 拓樸預設顯示商品、購物車讀取／修改、結帳 request／logic 與 DB write 六個主要節點，並自動包含所有 warning／failing 節點。可用「全部觀測點」、搜尋或從證據定位查看其餘節點。圖上只畫可見節點之間的原有直接連線；API 與調查仍保留全部觀測。非 Shop 拓樸維持全量顯示。

Monitor log 在瀏覽器保留最近 200 筆，列表顯示最新 50 筆，每 500ms 合併更新一次；切到報告、其他頁面或背景分頁時不重繪隱藏列表。原始 JSONL 不受影響，瀏覽器紀錄仍不補送斷線期間事件。Graph SSE 維持每 5 秒更新，重複快照不重繪；節點 DOM 與已展開的節點詳情盡可能保留，量測更新不會重建整份調查報告。

顯示篩選與 log 容量測試：`node --experimental-default-type=module --test console/tests/live-performance.test.mjs`。

即時與歷史拓樸共用卡片式呈現，依可見節點的呼叫方向分層；同一組節點與連線維持相同位置。切換顯示範圍或拓樸改變時重新排版。卡片保留原始 ID，主要量測優先採用後端指定軸；未指定時一般節點優先顯示 P95，queue／volume 優先顯示飽和度，再使用其他可用量測。歷史模式不受即時 SSE 覆蓋，右側調查及 Log 維持即時。

## 全螢幕拓樸

工具列的「全螢幕」會讓服務圖佔滿瀏覽器視窗，進入時自動依寬高縮放，包含全部可見節點。可繼續搜尋、篩選、縮放與點選節點，下方顯示節點詳情；即時資料持續更新。全螢幕圖下方保留精簡的歷史時間軸，可拖曳、切換上一張／下一張或回到即時；來源列收起。時間軸與詳情高度改變時，自動配合剩餘空間縮放拓樸。按 Esc 或「離開全螢幕」返回，還原原本的縮放與捲動位置。

「符合視窗」會同時考慮圖的寬高，不再受 65% 下限限制；手動放大後仍可捲動查看較遠的節點。
