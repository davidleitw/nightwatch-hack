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

獨立的 uv 專案，需要 Python 3.11 以上。

其他前端 API 已掛到同一個 app，清單與操作範例見
[前端 API 文件](../FRONTEND-API.md)。新增介面的假資料預設關閉，
以環境 flag `NIGHTWATCH_MOCK_DATA=1` 啟用；既有 graph dummy 不受此 flag 影響。

一鍵背景啟動／重啟：從 repo 根目錄執行 `./guardroom/restart.sh`（預設 port 8001），
詳細說明見 [guardroom README](../../guardroom/README.md)。以下為手動開發啟動方式。

```sh
cd control/server
uv sync --locked
uv run uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

- `GET http://127.0.0.1:8000/api/graph`：回傳 graph JSON 假資料，包含 10 個節點與 9 條示範連線。
- `http://127.0.0.1:8000/docs`：Swagger UI，含 response schema。
- `http://127.0.0.1:8000/openapi.json`：完整 OpenAPI 定義。

```sh
curl http://127.0.0.1:8000/api/graph
curl 'http://127.0.0.1:8000/api/graph?state=normal'
curl 'http://127.0.0.1:8000/api/graph?state=problem'
```

`state` 預設 `normal`；`problem` 模擬 payment 故障及 checkout、frontend 受影響。
參數僅影響當次回應，其他值回傳 HTTP 422。

回傳的是符合 graph schema 的資料實例；schema 定義可從 OpenAPI 取得。
沿用前端草案的 `nightwatch.snapshot.v2` 欄位格式。所有量測及連線都是
示範資料，時間與 seq 固定；未連接 monitor、Prometheus 或 Jaeger，
所以 sources 的 ok 與 edges 的 observed 均為 false。
可修改 `main.py` 的 `dummy_graph()` 調整示範節點與連線。
