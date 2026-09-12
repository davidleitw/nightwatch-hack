# NightWatch control server

Run the existing FastAPI app with `uv run --locked uvicorn main:app --host
127.0.0.1 --port 8001` from this directory. The investigation manager stores
sessions in `../.data/investigations.sqlite3` by default; set
`NIGHTWATCH_INVESTIGATION_DB` to use another file and
`NIGHTWATCH_GRAPH_URL` to select the operator configured graph source.

Production investigations use `NIGHTWATCH_LLM_API_KEY` (or
`OPENAI_API_KEY`) and `NIGHTWATCH_LLM_MODEL`. Offline tests may inject a Python
model factory through `install_investigations(..., model_factory=...)` or by
replacing `app.state.investigation_manager.model_factory` before application
startup. HTTP clients cannot select either the model or graph URL.

## Existing graph and legacy frontend endpoints

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

## SSE integration

`/events` has one handler. With `NIGHTWATCH_MOCK_DATA=1`, it retains the
legacy state/journal stream and also emits live `log` events. With mock data
off and no legacy state backend, it streams logs and heartbeats only, with
an initial comment declaring state unavailable; `/api/state` remains 503.
Cursor syntax is validated in both modes. Logs are live-only and do not
participate in incident cursor replay. The graph watcher and investigation
manager both run under the composed application lifespan.
