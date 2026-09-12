# NightWatch 觀測介面

預設開啟真實調查工作區：讀取 `/api/investigations/state`、歷史調查與 SSE，透過 `POST /api/investigations` 建立調查。即時 monitor log 使用另一條 `/events` 連線。

## 建置與啟動

從 repo 根目錄執行，不需要第三方套件：

```sh
python3 console/build.py
python3 console/serve.py --port 4173 --control-url http://127.0.0.1:8001
```

開啟 `http://127.0.0.1:4173/`。server 只供應 `dist/` 建置產物，代理 `/api/*` GET、`/events` 與建立調查 POST。其他 POST 回 405；上游失敗顯示錯誤，不切換成 mock。

`NIGHTWATCH_CONTROL_URL` 可設定上游；未設 URL 時使用 `--control-port`（預設 8001）。

## 明確啟用的示範

```sh
python3 console/serve.py --port 4174 --mock
```

`--mock` 使用 `mock_control.py` 的本機模擬 API，不支援建立真實調查；不可與 `--control-url` 或 `NIGHTWATCH_CONTROL_URL` 同時使用。

任一靜態服務的 `/?source=recording` 會切到舊事故錄影。`app.js`、`data.js`、`incident-template.html` 與 `contracts/fixtures/catalog_pool_leak/` 仍支援這兩種明確選取的示範，因此保留。

control 自己的 `NIGHTWATCH_MOCK_DATA=1` 是另一個獨立開關；它不等於 console 的 `--mock`。預設調查工作區不會因 control 的舊 state API 啟用 mock 就自動切換。

## 目前限制

畫面沒有故障注入、批准修復、換輪或對話入口。`report`、`snapshots`、`export` 專用端點已有後端實作，但目前畫面讀調查 detail／events／context，沒有獨立匯出操作。圖上監測範圍與未對接項目見 [盤點](../INTEGRATION.md)。
