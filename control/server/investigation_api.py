"""Persistent investigation manager and the typed ``/api/investigations`` API."""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Literal
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from frontend_api import APIError
from detector import GraphDetector
from frontend_live import saved_snapshots
from investigation_store import ActiveInvestigation, InvestigationStore, RequestConflict
from nightwatch_agent.graph import GraphAPI
from nightwatch_agent.loop import Limits, investigate, safe_error
from nightwatch_agent.prompts import tool_description
from nightwatch_agent.report import InvestigationReportBody


Json = dict[str, Any]
SCHEMAS = Path(__file__).resolve().parents[2] / "contracts" / "schemas"
LOG = logging.getLogger(__name__)


class InvestigationAPIError(APIError):
    """Keeps the legacy frontend APIError handler and headers untouched."""


class Trigger(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: Literal["manual", "detector"] = "manual"
    reason: str = Field(default="User requested investigation", min_length=1, pattern=r"\S")


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, pattern=r"\S")
    trigger: Trigger = Field(default_factory=Trigger)


class CreateResponse(BaseModel):
    investigation_id: str
    status: Literal["running"]


class InvestigationSummary(BaseModel):
    id: str
    created_seq: int
    status: Literal["running", "completed", "failed", "interrupted"]
    outcome: Literal["report_ready", "unresolved", "budget_exhausted", "execution_failed", "interrupted"] | None
    created_at: str
    started_at: str
    closed_at: str | None
    trigger: Trigger
    summary_zh: str
    event_seq: int


class SavedInvestigationReport(InvestigationReportBody):
    schema_version: Literal["nightwatch.investigation-report.v1"]


class InvestigationReport(BaseModel):
    investigation_id: str
    outcome: str
    summary_zh: str
    started_at: str
    closed_at: str
    agent_report: dict[str, Any] | None
    investigation_report: SavedInvestigationReport | None = None
    evidence_ids: list[str]
    limitations: list[str]


class InvestigationDetail(InvestigationSummary):
    request_id: str
    request: dict[str, Any]
    report: InvestigationReport | None
    evidence: list[dict[str, Any]]
    usage: dict[str, Any]
    context_available: bool
    context_complete: bool


class InvestigationEvent(BaseModel):
    cursor: int
    investigation_id: str
    seq: int
    type: Literal["investigation.started", "tool.started", "observation.recorded", "tool.failed", "report.submitted", "investigation.finished"]
    at: str
    payload: dict[str, Any]


class InvestigationState(BaseModel):
    schema_version: Literal["nightwatch.investigation-state.v1"]
    server_now: str
    cursor: int
    active_investigation_id: str | None
    active_investigation: InvestigationSummary | None
    last_completed_investigation_id: str | None
    graph: dict[str, Any] | None
    graph_received_at: str | None
    graph_error: str | None


class InvestigationList(BaseModel):
    items: list[InvestigationSummary]
    next_before: int | None


class InvestigationEventList(BaseModel):
    items: list[InvestigationEvent]
    next_after: int | None


class InvestigationSnapshot(BaseModel):
    evidence_id: str
    snapshot: dict[str, Any]


class InvestigationSnapshots(BaseModel):
    investigation_id: str
    snapshots: list[InvestigationSnapshot]


class InvestigationContext(BaseModel):
    complete: bool
    context: dict[str, Any]


class InvestigationExport(BaseModel):
    schema_version: Literal["nightwatch.investigation-export.v1"]
    exported_at: str
    complete: bool
    session: dict[str, Any]
    session_start: InvestigationEvent | None
    session_end: InvestigationEvent | None
    report: InvestigationReport | None
    context: InvestigationContext
    events: list[InvestigationEvent]
    evidence: list[dict[str, Any]]
    usage: dict[str, Any]


def _timestamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _project_graph(graph: Json) -> Json:
    graph = copy.deepcopy(graph)
    for node in graph.get("nodes", []):
        node["assessment"] = "unassessed"
    return graph


def _model_name(model: Any) -> str:
    return str(model if isinstance(model, str) else getattr(model, "model_name", type(model).__name__))


class InvestigationManager:
    """Owns one application-scoped worker and the observation refresh task."""

    def __init__(self, *, db_path: str | os.PathLike[str] | None = None,
                 model_factory: Callable[[], Any] | None = None,
                 graph_factory: Callable[[], GraphAPI] | None = None,
                 graph_url: str | None = None, limits: Limits | None = None):
        self.store = InvestigationStore(db_path)
        self.model_factory = model_factory or self._default_model
        self.graph_url = graph_url or os.environ.get("NIGHTWATCH_GRAPH_URL", "http://127.0.0.1:8001/api/graph")
        self.graph_factory = graph_factory or (lambda: GraphAPI(self.graph_url, SCHEMAS))
        self.limits = limits or Limits()
        self.graph: Json | None = None
        self.graph_received_at: str | None = None
        self.graph_error: str | None = None
        self._graph_api: GraphAPI | None = None
        self._graph_task: asyncio.Task | None = None
        self._active_task: asyncio.Task | None = None
        self._active_id: str | None = None
        self._stopping = False
        self._started = False
        self._clients: list[Any] = []
        self._detector = GraphDetector()
        # Operator-selected previews/history and mock UI mode are never inputs
        # to automatic model calls. Manual investigation behavior is unchanged.
        self._detection_enabled = not urlsplit(self.graph_url).query and os.getenv("NIGHTWATCH_MOCK_DATA") != "1"

    def _default_model(self):
        from openai import AsyncOpenAI
        from pydantic_ai.models.openai import OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider
        key = os.environ.get("NIGHTWATCH_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("請設定 NIGHTWATCH_LLM_API_KEY 或 OPENAI_API_KEY")
        endpoint = os.environ.get("NIGHTWATCH_LLM_ENDPOINT", "https://api.openai.com/v1/responses")
        base_url = endpoint.removesuffix("/").removesuffix("/responses")
        client = AsyncOpenAI(api_key=key, base_url=base_url, max_retries=2, timeout=60)
        self._clients.append(client)
        return OpenAIResponsesModel(os.environ.get("NIGHTWATCH_LLM_MODEL", "gpt-6-astra"),
                                    provider=OpenAIProvider(openai_client=client))

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stopping = False
        self.store.recover_running()
        # A first refresh is awaited so state has a useful graph immediately
        # after startup when the configured source is reachable.
        await self._refresh_graph()
        self._graph_task = asyncio.create_task(self._graph_loop(), name="nightwatch-investigation-graph")

    async def stop(self) -> None:
        if not self._started:
            self.store.close()
            return
        self._stopping = True
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()
            await asyncio.gather(self._active_task, return_exceptions=True)
        if self._graph_task:
            self._graph_task.cancel()
            await asyncio.gather(self._graph_task, return_exceptions=True)
        for client in self._clients:
            close = getattr(client, "close", None)
            if close:
                result = close()
                if inspect.isawaitable(result):
                    await result
        self._clients.clear()
        self.store.close()
        self._started = False

    async def _graph_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(5)
            if not self._stopping:
                await self._refresh_graph()

    async def _refresh_graph(self) -> None:
        try:
            if self._graph_api is None:
                self._graph_api = self.graph_factory()
            raw = await asyncio.to_thread(self._graph_api._read)
            self.graph = _project_graph(raw)
            self.graph_received_at = _timestamp()
            self.graph_error = None
        except Exception as error:
            self.graph_error = safe_error(error)
            self._detector.reset()
            return
        if self._detection_enabled and not self._stopping:
            try:
                await self._detect_graph()
            except Exception:
                self._detector.reset()
                LOG.exception("Automatic investigation detection failed; retrying next graph refresh")

    async def _detect_graph(self) -> None:
        detection = self.store.current_detection(self.graph_url)
        fresh, payload, recovered = self._detector.observe(
            self.graph, detection["payload"]["node_ids"] if detection else None)
        if not fresh:
            return
        if detection is not None:
            if recovered and self.store.state_base()["active_investigation_id"] is None:
                self.store.release_detection(detection["request_id"])
                self._detector.reset()
                LOG.info("Automatic investigation rearmed after three healthy snapshots: %s", detection["request_id"])
                return
            if detection["investigation_id"] is not None:
                return
            # A detection waiting behind a manual investigation, or interrupted
            # before admission, must still be abnormal in the current graph.
            if not any(node["id"] in detection["payload"]["node_ids"] and
                       node["status"] in {"warning", "failing"} for node in self.graph["nodes"]):
                return
        elif payload is not None:
            request_id = "detector-" + uuid4().hex
            self.store.latch_detection(self.graph_url, request_id, payload)
            detection = {"request_id": request_id, "payload": payload}
            self._detector.reset()
            LOG.info("Automatic investigation latched: %s; %s", request_id, payload["reason"])
        else:
            return
        trigger = {"source": "detector", "reason": detection["payload"]["reason"]}
        body = {"request_id": detection["request_id"], "trigger": trigger}
        try:
            await self.create(detection["request_id"], body, trigger)
        except InvestigationAPIError as error:
            if error.body["error"]["code"] != "investigation_active":
                raise
            # The durable latch remains pending; no second queue/worker exists.

    def state(self) -> Json:
        base = self.store.state_base()
        return {"schema_version": "nightwatch.investigation-state.v1", "server_now": _timestamp(), **base,
                "graph": copy.deepcopy(self.graph), "graph_received_at": self.graph_received_at,
                "graph_error": self.graph_error}

    async def create(self, request_id: str, request_body: Json, trigger: Json) -> tuple[str, Json]:
        investigation_id = "inv-" + uuid4().hex
        try:
            kind, summary = self.store.create(investigation_id, request_id, request_body, trigger)
        except RequestConflict as error:
            raise InvestigationAPIError(409, "request_conflict", "同一 request_id 已用於不同的調查內容") from error
        except ActiveInvestigation as error:
            api_error = InvestigationAPIError(409, "investigation_active", "已有一件調查正在執行")
            api_error.body["error"]["details"] = {"active_investigation_id": error.investigation_id}
            raise api_error from error
        if kind == "replay":
            return summary["id"], summary
        self._active_id = investigation_id
        self._active_task = asyncio.create_task(self._run(investigation_id), name=f"nightwatch-investigation-{investigation_id}")
        return investigation_id, summary

    async def _run(self, investigation_id: str) -> None:
        evidence: list[Json] = []
        usage: Json = {}
        context: Json | None = None
        try:
            data = self.graph_factory()
            await data.prepare()
            detection = self.store.detection_context(investigation_id)
            if detection is not None:
                data.opening = copy.deepcopy(data.opening)
                data.opening["trigger"] = {"source": "detector", "reason": detection["reason"]}
                data.opening["detection"] = detection
                data.opening["data_scope"] = data.opening["data_scope"].replace(
                    "No incident has been detected by this runner. ",
                    "The backend detected repeated abnormal monitor status; detection is a symptom, not a root-cause conclusion. ")
                data.opening["task"] = (
                    "Investigate the supplied detection and its saved snapshot. Compare current and retained "
                    "snapshots around detected_at, prioritizing node_ids. The supplied detection is context, "
                    "not a tool evidence ID; obtain tool evidence before citing conclusions. Explain observed "
                    "changes, hypotheses, counterevidence, limitations and the next evidence needed.")
            model = self.model_factory()
            if inspect.isawaitable(model):
                model = await model

            def on_context(value: Json) -> None:
                nonlocal context
                context = copy.deepcopy(value)
                self.store.update_context(investigation_id, context, False)

            def on_event(event: Json) -> None:
                event_type = event.get("type")
                if event_type == "investigation.finished":
                    return
                self.store.append_event(investigation_id, event_type, copy.deepcopy(event.get("payload", {})))

            result = await investigate(model=model, capabilities=data.capabilities, opening=data.opening,
                                       report_schema=json.loads((SCHEMAS / "agent-report.schema.json").read_text()),
                                       backend=data.query, limits=self.limits, on_event=on_event,
                                       on_context=on_context)
            evidence, usage = result.evidence, result.usage
            if context is not None:
                context["messages"] = context.get("messages", [])
            limitations = []
            if result.report is None:
                limitations.append("模型未提交有效的結構化調查報告；原因與已取得證據仍保存。")
            if data.opening.get("mode") == "graph_api":
                limitations.append("觀測來源是設定的 Guard Room API；alive 表示窗口內有事件，不等同服務探活。資料是否新鮮依快照時間及來源欄位判讀。")
            self.store.finish(investigation_id, "completed", result.status, result.reason, result.report,
                              evidence, usage, context, limitations, True)
        except asyncio.CancelledError:
            # Cancellation is the orderly shutdown signal.  The persisted
            # context/tool events remain and a single terminal event is added.
            self.store.finish(investigation_id, "interrupted", "interrupted", "服務關閉，調查在完成前被中斷。",
                              None, evidence, usage, context, ["服務在調查完成前關閉；保存的 context 與工具事件可能不完整。"], False)
            raise
        except Exception as error:
            reason = safe_error(error)
            self.store.finish(investigation_id, "failed", "execution_failed", f"調查執行失敗：{reason}",
                              None, evidence, usage, context, ["調查在準備或執行期間發生錯誤。"], False)
        finally:
            if self._active_id == investigation_id:
                self._active_id = None
                self._active_task = None

    def close_sync(self) -> None:
        self.store.close()


def _error_response(error: APIError) -> JSONResponse:
    return JSONResponse(error.body, status_code=error.status, headers={"Cache-Control": "no-store"})


def _query(request: Request, allowed: set[str]) -> dict[str, str]:
    if set(request.query_params) - allowed or any(len(request.query_params.getlist(k)) != 1 for k in request.query_params):
        raise InvestigationAPIError(400, "invalid_request", "不支援或重複的查詢參數")
    return dict(request.query_params)


def _integer(value: str | None, *, name: str, minimum: int = 0, maximum: int | None = 9223372036854775807) -> int:
    if value is None or len(value) > 19 or not re.fullmatch(r"[0-9]+", value):
        raise InvestigationAPIError(400, "invalid_request", f"{name} 必須是整數")
    result = int(value)
    if result < minimum or (maximum is not None and result > maximum):
        raise InvestigationAPIError(400, "invalid_request", f"{name} 超出允許範圍")
    return result


def install_investigations(app, *, model_factory: Callable[[], Any] | None = None,
                           graph_factory: Callable[[], GraphAPI] | None = None,
                           db_path: str | os.PathLike[str] | None = None,
                           graph_url: str | None = None, limits: Limits | None = None) -> InvestigationManager:
    """Install the manager and routes on an existing FastAPI app.

    ``model_factory`` is intentionally a Python dependency injection seam for
    offline tests.  Production leaves it unset and uses the configured model
    environment; HTTP clients cannot select a model or graph source.
    """
    old = getattr(app.state, "investigation_manager", None)
    if old is not None:
        old.close_sync()
    manager = InvestigationManager(db_path=db_path, model_factory=model_factory,
                                   graph_factory=graph_factory, graph_url=graph_url, limits=limits)
    app.state.investigation_manager = manager

    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous_lifespan(application):
            await manager.start()
            try:
                yield
            finally:
                await manager.stop()

    app.router.lifespan_context = lifespan
    headers = {"Cache-Control": "no-store"}

    async def create(request: Request):
        _query(request, set())
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise InvestigationAPIError(400, "invalid_request", "Content-Type 必須是 application/json")
        try:
            raw = await request.body()
            body = CreateRequest.model_validate_json(raw)
        except (ValidationError, ValueError):
            raise InvestigationAPIError(400, "invalid_request", "JSON 欄位無效，請提供 request_id 與可選 trigger") from None
        request_body = body.model_dump(mode="json")
        trigger = request_body["trigger"]
        investigation_id, summary = await manager.create(body.request_id, request_body, trigger)
        # Replay returns the original identity, while all accepted creates
        # retain the documented lightweight 202 response.
        return JSONResponse({"investigation_id": investigation_id, "status": "running"}, status_code=202, headers=headers)

    async def state(request: Request):
        _query(request, set())
        return JSONResponse(manager.state(), headers=headers)

    async def listing(request: Request):
        query = _query(request, {"limit", "before"})
        limit = _integer(query.get("limit", "20"), name="limit", minimum=1, maximum=100)
        before = _integer(query["before"], name="before", minimum=1) if "before" in query else None
        return JSONResponse(manager.store.list(limit, before), headers=headers)

    async def detail(request: Request):
        _query(request, set())
        identifier = request.path_params["id"]
        if not identifier.strip():
            raise InvestigationAPIError(400, "invalid_request", "id 不可為空")
        value = manager.store.detail(identifier)
        if value is None:
            raise InvestigationAPIError(404, "not_found", "找不到這件調查")
        return JSONResponse(value, headers=headers)

    async def report(request: Request):
        response = await detail(request)
        value = json.loads(response.body)
        if value["report"] is None:
            raise InvestigationAPIError(409, "investigation_active", "調查尚未結束，報告尚未產生")
        return JSONResponse(value["report"], headers=headers)

    async def snapshots(request: Request):
        response = await detail(request)
        value = json.loads(response.body)
        return JSONResponse({"investigation_id": value["id"], "snapshots": saved_snapshots(value)}, headers=headers)

    async def events(request: Request):
        query = _query(request, {"after", "limit"})
        if not request.path_params["id"].strip():
            raise InvestigationAPIError(400, "invalid_request", "id 不可為空")
        after = _integer(query.get("after", "0"), name="after", minimum=0)
        limit = _integer(query.get("limit", "100"), name="limit", minimum=1, maximum=500)
        value = manager.store.events(request.path_params["id"], after, limit)
        if value is None:
            raise InvestigationAPIError(404, "not_found", "找不到這件調查")
        return JSONResponse(value, headers=headers)

    async def context(request: Request):
        _query(request, set())
        if not request.path_params["id"].strip():
            raise InvestigationAPIError(400, "invalid_request", "id 不可為空")
        value = manager.store.context(request.path_params["id"])
        if value is None:
            raise InvestigationAPIError(404, "not_found", "找不到這件調查")
        return JSONResponse(value, headers=headers)

    async def export(request: Request):
        _query(request, set())
        identifier = request.path_params["id"]
        if not identifier.strip():
            raise InvestigationAPIError(400, "invalid_request", "id 不可為空")
        value = manager.store.export(identifier)
        if value is None:
            raise InvestigationAPIError(404, "not_found", "找不到這件調查")
        return JSONResponse(value, headers=headers)

    async def stream(request: Request):
        query = _query(request, {"after"})
        query_after = _integer(query["after"], name="after", minimum=0) if "after" in query else None
        header = request.headers.get("last-event-id")
        header_after = _integer(header, name="Last-Event-ID", minimum=0) if header is not None else None
        if query_after is not None and header_after is not None and query_after != header_after:
            raise InvestigationAPIError(400, "invalid_request", "after 與 Last-Event-ID 必須一致")
        requested = query_after if query_after is not None else header_after
        current_cursor = manager.store.cursor()
        if requested is not None and requested > current_cursor:
            raise InvestigationAPIError(400, "invalid_request", "cursor 超過目前事件版本")
        initial = manager.state()
        # A no-cursor connection starts after the atomically captured state
        # watermark; reconnects replay from the requested exclusive cursor.
        last_event_cursor = initial["cursor"] if requested is None else requested

        def frame(name: str, payload: Json, cursor: int | None = None) -> str:
            prefix = f"id: {cursor}\n" if name == "investigation" and cursor is not None else ""
            return prefix + f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"

        async def generator():
            nonlocal last_event_cursor
            previous_state = initial
            yield frame("state", initial)
            if initial["graph"] is not None:
                yield frame("graph", initial["graph"])
            heartbeat = time.monotonic() + 2
            graph_tick = time.monotonic() + 5
            try:
                while not await request.is_disconnected() and not manager._stopping:
                    current = manager.state()
                    if (current["cursor"] != previous_state["cursor"] or
                            current["active_investigation_id"] != previous_state["active_investigation_id"] or
                            current["last_completed_investigation_id"] != previous_state["last_completed_investigation_id"] or
                            current["graph_error"] != previous_state["graph_error"]):
                        yield frame("state", current)
                    events = manager.store.events_after_cursor(last_event_cursor, 100)
                    for event in events:
                        yield frame("investigation", event, event["cursor"])
                        last_event_cursor = event["cursor"]
                    if time.monotonic() >= graph_tick:
                        if current["graph"] is not None:
                            yield frame("graph", current["graph"])
                        graph_tick = time.monotonic() + 5
                    if time.monotonic() >= heartbeat:
                        yield frame("ping", {"server_now": _timestamp()})
                        heartbeat = time.monotonic() + 2
                    previous_state = current
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                return

        return StreamingResponse(generator(), media_type="text/event-stream",
                                 headers={**headers, "X-Accel-Buffering": "no"})

    # Register state/listing before the variable path so /state cannot be
    # interpreted as an investigation ID.
    app.add_api_route("/api/investigations", create, methods=["POST"], response_model=CreateResponse,
                      status_code=202, tags=["Investigations"], summary="Start a persistent investigation")
    app.add_api_route("/api/investigations", listing, methods=["GET"], response_model=InvestigationList,
                      tags=["Investigations"], summary="List investigation sessions")
    app.add_api_route("/api/investigations/state", state, methods=["GET"], response_model=InvestigationState,
                      tags=["Investigations"], summary="Get current investigation state")
    app.add_api_route("/api/investigations/stream", stream, methods=["GET"], response_class=StreamingResponse,
                      tags=["Investigations"], summary="Stream investigation and graph events")
    app.add_api_route("/api/investigations/{id}", detail, methods=["GET"], response_model=InvestigationDetail,
                      tags=["Investigations"], summary="Get an investigation report")
    app.add_api_route("/api/investigations/{id}/events", events, methods=["GET"], response_model=InvestigationEventList,
                      tags=["Investigations"], summary="List persisted investigation events")
    app.add_api_route("/api/investigations/{id}/context", context, methods=["GET"], response_model=InvestigationContext,
                      tags=["Investigations"], summary="Get the saved model context")

    app.add_api_route("/api/investigations/{id}/report", report, methods=["GET"], response_model=InvestigationReport,
                      tags=["Investigations"], summary="Read the saved terminal investigation report",
                      responses={404: {"description": "Unknown investigation"}, 409: {"description": "Investigation still running"}})
    app.add_api_route("/api/investigations/{id}/snapshots", snapshots, methods=["GET"], response_model=InvestigationSnapshots,
                      tags=["Investigations"], summary="Read exact graph snapshots retained as investigation evidence")

    app.add_api_route("/api/investigations/{id}/export", export, methods=["GET"], response_model=InvestigationExport,
                      tags=["Investigations"], summary="Export session, report, transcript, events, evidence and usage",
                      description="Returns saved data only. Running or interrupted sessions are marked incomplete; does not invoke the model or refresh observations.")

    @app.exception_handler(InvestigationAPIError)
    async def investigation_error(request: Request, error: APIError):
        return _error_response(error)

    return manager
