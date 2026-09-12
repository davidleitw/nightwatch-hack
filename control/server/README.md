# Control graph server

獨立 uv 專案，Python 3.11+。啟動時讀 `GUARDROOM_CONFIG`，預設
`guardroom/shop-web.config.json`。config 提供拓撲，monitor log 提供節點量測，
graph state 儲存於本機 JSON checkpoint，重啟後接續讀取。

```sh
./guardroom/restart.sh
curl http://127.0.0.1:8001/api/graph
```

手動開發：

```sh
cd control/server
uv sync --locked
uv run uvicorn main:app --reload --host 127.0.0.1 --port 8001
```

完整流程、config、snapshot、shop-web 操作範例與限制見
[Guard Room README](../../guardroom/README.md)。僅支援單一 worker。

- GET /api/graph：最新 monitor graph。
- GET /api/graph?state=normal|problem：dummy 預覽。
- POST /api/logs：接收並持久化 log、更新 graph。
- GET /events：即時 SSE log。
- /docs、/openapi.json：API 文件。

其他前端 API 見 [前端 API 文件](../FRONTEND-API.md)，其 mock 預設關閉，
可用 `NIGHTWATCH_MOCK_DATA=1` 啟用；live graph 尚未接入 /api/state 與 SSE graph。
