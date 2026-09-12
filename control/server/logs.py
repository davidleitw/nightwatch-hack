"""Console log validation, bounded deduplication and live fan-out."""
import asyncio
from collections import OrderedDict
from datetime import datetime
import json
from typing import Annotated, Literal

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[str, Field(min_length=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LogRefs(StrictModel):
    node_ids: list[Identifier]

    @field_validator("node_ids")
    @classmethod
    def unique_nodes(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("node_ids must be unique")
        return value


class ConsoleLog(StrictModel):
    schema_version: Literal["nightwatch.log.v1"]
    event_id: Identifier
    monitor_id: Identifier
    occurred_at: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")]
    level: Literal["TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"]
    message: str
    refs: LogRefs
    instance_id: Identifier | None = None
    invocation_id: Identifier | None = None
    attributes: dict | None = None

    @field_validator("occurred_at")
    @classmethod
    def valid_datetime(cls, value):
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @model_validator(mode="before")
    @classmethod
    def optional_fields_cannot_be_null(cls, value):
        if isinstance(value, dict):
            for key in ("instance_id", "invocation_id", "attributes"):
                if key in value and value[key] is None:
                    raise ValueError(f"{key} must be omitted instead of null")
        return value


class LogBatch(StrictModel):
    logs: list[ConsoleLog] = Field(min_length=1, max_length=50)


class LogReceipt(StrictModel):
    accepted: int
    duplicates: int


class LogHub:
    """Owned by one ASGI event loop; no awaits during acceptance/fan-out."""
    def __init__(self, capacity=10000, subscriber_capacity=256, store=None):
        self.capacity = capacity
        self.subscriber_capacity = subscriber_capacity
        self.recent = OrderedDict()
        self.subscribers = set()
        self.store = store

    def accept(self, logs: list[ConsoleLog]) -> LogReceipt:
        if self.store is not None:
            fresh = self.store.commit([log.model_dump(exclude_none=True) for log in logs])
            self.publish(fresh)
            return LogReceipt(accepted=len(fresh), duplicates=len(logs) - len(fresh))
        accepted = 0
        for log in logs:
            key = (log.monitor_id, log.event_id)
            if key in self.recent:
                continue
            payload = log.model_dump(exclude_none=True)
            self.recent[key] = payload
            if len(self.recent) > self.capacity:
                self.recent.popitem(last=False)
            accepted += 1
            self.publish([payload])
        return LogReceipt(accepted=accepted, duplicates=len(logs) - accepted)

    def publish(self, payloads):
        for payload in payloads:
            for queue in tuple(self.subscribers):
                if queue.full():
                    # Disconnect slow consumers instead of blocking ingestion.
                    while not queue.empty():
                        queue.get_nowait()
                    queue.put_nowait(None)
                    self.subscribers.discard(queue)
                else:
                    queue.put_nowait(payload)

    def subscribe(self):
        queue = asyncio.Queue(maxsize=self.subscriber_capacity)
        self.subscribers.add(queue)
        return queue


def create_log_router(hub: LogHub, *, include_events=True) -> APIRouter:
    router = APIRouter()

    @router.post("/api/logs", response_model=LogReceipt)
    async def ingest_logs(batch: LogBatch):
        return hub.accept(batch.logs)

    async def events():
        async def stream():
            queue = hub.subscribe()
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=2)
                    except asyncio.TimeoutError:
                        yield "event: ping\ndata: {}\n\n"
                        continue
                    if payload is None:
                        break
                    yield "event: log\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            finally:
                hub.subscribers.discard(queue)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    if include_events:
        router.add_api_route("/events", events, methods=["GET"])
    return router
