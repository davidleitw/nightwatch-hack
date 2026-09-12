"""Frontend routes installed on the existing Guard Room FastAPI application."""

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import time

from fastapi import Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from frontend_mock import MockStore, timestamp


LOG = logging.getLogger(__name__)
SCHEMAS = Path(__file__).resolve().parents[2] / "contracts" / "schemas"


class APIError(Exception):
    def __init__(self, status, code, message):
        self.status = status
        self.body = {"error": {"code": code, "message_zh": message, "details": {}}}
        super().__init__(message)


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, pattern=r"\S")


class InjectBody(RequestBody):
    card_id: str = Field(min_length=1, pattern=r"\S")
    lease_secs: int = Field(default=1200, ge=901)


class RestoreAllBody(RequestBody):
    force: bool


class ApproveBody(RequestBody):
    proposal_id: str = Field(min_length=1, pattern=r"\S")


READ_ROUTES = {
    "/health": ("health", None, "HTTP 服務存活"),
    "/api/readiness": ("readiness", "readiness", "就緒狀態"),
    "/api/state": ("state", "state", "完整初始狀態"),
    "/api/capabilities": ("capabilities", None, "節點與能力表"),
    "/api/debug/logs": ("logs", None, "近期日誌"),
    "/api/faults/catalog": ("catalog", "fault-catalog", "故障卡目錄"),
    "/api/faults/instances": ("faults", "faults", "故障實例"),
    "/api/rounds/operations/{id}": ("operation", None, "操作進度"),
    "/api/incidents": ("incidents", None, "事故清單"),
    "/api/incidents/{id}": ("incident", None, "事故詳情"),
    "/api/incidents/{id}/snapshots": ("snapshots", None, "事故快照"),
    "/api/incidents/{id}/timeline": ("timeline", "timeline", "事故時間線"),
    "/api/incidents/{id}/report": ("report", "report", "結案報告"),
    "/api/incidents/{id}/events": ("incident_events", None, "事故事件紀錄"),
}
WRITE_ROUTES = {
    "/api/faults": ("inject", InjectBody, "注入故障"),
    "/api/faults/instances/{id}/restore": ("restore", RequestBody, "還原單一故障"),
    "/api/faults/restore-all": ("restore_all", RestoreAllBody, "清理故障"),
    "/api/rounds": ("round", RequestBody, "開始下一輪"),
    "/api/incidents/{id}/approve": ("approve", ApproveBody, "批准提案"),
    "/api/incidents/{id}/abort": ("abort", RequestBody, "中止事故"),
}


def unavailable_readiness():
    ids = ("prometheus_reachable", "jaeger_reachable", "logstore_receiving", "nodes_alive", "shopper_rate", "baseline", "model", "fault_clear")
    return {"ready": False, "checks": [
        {"id": key, "status": "failed" if key == "model" else "waiting",
         "detail_zh": "尚未接入模型" if key == "model" else "尚未接入真實資料來源"} for key in ids],
        "next_step_zh": "等待服務啟動"}


def install_frontend(app, graph_provider):
    flag = os.getenv("NIGHTWATCH_MOCK_DATA", "0")
    if flag not in ("0", "1"):
        raise ValueError("NIGHTWATCH_MOCK_DATA 只能是 0 或 1")
    mock_data = flag == "1"
    store = MockStore(APIError, graph_provider) if mock_data else None
    app.state.frontend_store = store
    headers = {"X-NightWatch-Mock": str(mock_data).lower(), "Cache-Control": "no-store"}
    if mock_data:
        LOG.warning("[MOCK DATA ENABLED] 前端 API 全部使用記憶體假資料；不會操作真實服務")

    def required_store():
        if store is None:
            raise APIError(503, "internal", "真實後端尚未接入；沒有提供假資料或執行任何操作")
        return store

    def response(data, status=200):
        return JSONResponse(data, status_code=status, headers=headers)

    @app.exception_handler(APIError)
    async def api_error(request, error):
        return response(error.body, error.status)

    def check_query(request, allowed=()):
        query = request.query_params
        if set(query) - set(allowed) or any(len(query.getlist(key)) != 1 for key in query):
            raise APIError(400, "invalid_request", "不支援或重複的查詢參數")
        return dict(query)

    def read_endpoint(name):
        async def endpoint(request: Request):
            query = check_query(request, ("service", "limit") if name == "logs" else ())
            if "limit" in query and (not re.fullmatch(r"[0-9]+", query["limit"]) or not 1 <= int(query["limit"]) <= 20000):
                raise APIError(400, "invalid_request", "limit 必須是 1–20000 的整數")
            if name == "health":
                data = {"status": "ok"}
            elif name == "readiness" and store is None:
                data = unavailable_readiness()
            elif name in ("state", "readiness", "capabilities"):
                state = required_store().stream_snapshot()[0]
                data = state if name == "state" else state[name]
            else:
                data = required_store().read(name, request.path_params, query)
            return response(data)
        return endpoint

    def write_endpoint(name, model):
        async def endpoint(request: Request):
            check_query(request)
            if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                raise APIError(400, "invalid_request", "Content-Type 必須是 application/json")
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 262144:
                    raise APIError(400, "invalid_request", "請求最多 256 KiB")
            try:
                body = model.model_validate_json(raw).model_dump(exclude_unset=True)
            except ValidationError:
                raise APIError(400, "invalid_request", "JSON 欄位無效，請依 OpenAPI 提供 request_id 與必要欄位") from None
            result = required_store().execute(name, request.path_params, body, request.url.path)
            return response(result, 202)
        return endpoint

    error_schema = {"type": "object", "required": ["error"], "properties": {
        "error": {"type": "object", "required": ["code", "message_zh", "details"], "properties": {
            "code": {"type": "string"}, "message_zh": {"type": "string"}, "details": {"type": "object"}}}}}
    errors = {code: {"description": message, "content": {"application/json": {"schema": error_schema}}}
              for code, message in [(400, "參數錯誤"), (404, "找不到資源"), (409, "目前狀態不允許操作"), (503, "真實後端尚未接入")]}

    def path_parameters(path):
        return [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}] if "{id}" in path else []

    for path, (name, schema, title) in READ_ROUTES.items():
        shape = {"$ref": f"#/components/schemas/{schema}"} if schema else {"type": "object"}
        if name in ("logs", "incidents", "incident_events"):
            shape = {"type": "array", "items": {"$ref": "#/components/schemas/incident-commit"} if name == "incident_events" else {"type": "object"}}
        parameters = path_parameters(path)
        if name == "logs":
            parameters += [{"name": "service", "in": "query", "schema": {"type": "string"}},
                           {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 20000, "default": 20}}]
        app.add_api_route(path, read_endpoint(name), methods=["GET"], name=f"frontend_{name}", summary=title,
                          tags=["Frontend"], responses={200: {"content": {"application/json": {"schema": shape}}}, **errors},
                          openapi_extra={"parameters": parameters})
    for path, (name, model, title) in WRITE_ROUTES.items():
        fields = ["operation_id"] + (["instance_id"] if name == "inject" else ["run_id"] if name == "round" else [])
        shape = {"type": "object", "required": fields, "properties": {key: {"type": "string"} for key in fields}}
        app.add_api_route(path, write_endpoint(name, model), methods=["POST"], name=f"frontend_{name}", summary=title,
                          status_code=202, tags=["Frontend"], responses={202: {"content": {"application/json": {"schema": shape}}}, **errors},
                          openapi_extra={"parameters": path_parameters(path), "requestBody": {"required": True,
                              "content": {"application/json": {"schema": model.model_json_schema()}}}})

    @app.get("/events", tags=["Frontend"], summary="前端 SSE", response_class=StreamingResponse,
             responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}, **errors},
             openapi_extra={"parameters": [{"name": "cursor", "in": "query", "schema": {"type": "string", "pattern": "^[^:]+:[0-9]+$"}}]})
    async def events(request: Request):
        query = check_query(request, ("cursor",))
        cursor = query.get("cursor", request.headers.get("last-event-id"))
        if cursor is not None and not re.fullmatch(r"[^:]+:[0-9]+", cursor):
            raise APIError(400, "invalid_request", "cursor 必須是 <run_id>:<revision>")
        state, journal = required_store().stream_snapshot()
        revision = journal[-1]["revision"] if journal else 0
        if cursor:
            cursor_run, cursor_revision = cursor.rsplit(":", 1)
            if cursor_run == state["run"]["id"]:
                if int(cursor_revision) > revision:
                    raise APIError(400, "invalid_request", "cursor 超過目前事件版本")
                revision = int(cursor_revision)

        def frame(name, data):
            event_id = f"id: {data['run_id']}:{data['revision']}\n" if name == "incident" else ""
            return f"{event_id}event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        async def stream():
            previous = state
            last_revision = revision
            yield frame("state", state)
            for commit in journal:
                if commit["revision"] > last_revision:
                    yield frame("incident", commit)
                    last_revision = commit["revision"]
            ping_at = time.monotonic() + 2
            while not await request.is_disconnected():
                current, commits = store.stream_snapshot()
                if current["run"]["id"] != previous["run"]["id"]:
                    last_revision = 0
                    yield frame("run", {"run_id": current["run"]["id"]})
                    yield frame("state", current)
                else:
                    for field in ("readiness", "faults"):
                        if current[field] != previous[field]:
                            yield frame(field, current[field])
                for commit in commits:
                    if commit["revision"] > last_revision:
                        yield frame("incident", commit)
                        last_revision = commit["revision"]
                if time.monotonic() >= ping_at:
                    yield frame("ping", {"server_now": timestamp()})
                    ping_at = time.monotonic() + 2
                previous = current
                await asyncio.sleep(0.2)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={**headers, "X-Accel-Buffering": "no"})

    def convert(value):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key == "$schema":
                    continue
                if key == "$ref":
                    filename, _, fragment = item.partition("#")
                    item = "#/components/schemas/" + filename.removesuffix(".schema.json") + fragment
                else:
                    item = convert(item)
                result[key] = item
            return result
        if isinstance(value, list):
            return [convert(item) for item in value]
        return value

    def openapi():
        if app.openapi_schema is None:
            document = get_openapi(title=app.title, version=app.version, routes=app.routes,
                                  description="Graph 維持既有 dummy API。舊版事故與操作介面以 NIGHTWATCH_MOCK_DATA=1 啟用假資料，新的 persistent investigation API 使用獨立的 SQLite session store；X-NightWatch-Mock 回應標頭標示舊版模式。")
            definitions = document.setdefault("components", {}).setdefault("schemas", {})
            for name in ("state", "readiness", "snapshot", "node", "edge", "faults", "fault-catalog", "report", "timeline", "agent-report", "incident-commit"):
                definitions[name] = convert(json.loads((SCHEMAS / f"{name}.schema.json").read_text()))
            app.openapi_schema = document
        return app.openapi_schema
    app.openapi = openapi
