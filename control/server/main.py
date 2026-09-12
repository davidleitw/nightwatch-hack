"""Config-driven graph snapshots from monitor logs, plus explicit mock previews."""

import asyncio
from contextlib import asynccontextmanager, suppress
import logging
import os
from pathlib import Path
from time import monotonic
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from logs import LogHub, create_log_router
from graph_state import GraphStore
from graph_history import TIMESTAMP_PATTERN, parse_timestamp

from frontend_api import install_frontend
from investigation_api import install_investigations


class Trend(BaseModel):
    errors: Literal["rising", "falling", "flat", "na"] = "flat"
    latency: Literal["rising", "falling", "flat", "na"] = "flat"
    saturation: Literal["rising", "falling", "flat", "na"] = "flat"


class Node(BaseModel):
    id: str = Field(min_length=1)
    kind: Literal["service", "datastore", "queue", "volume", "synthetic", "external"] = "service"
    traffic: float | None = 12.0
    errors: float | None = 0.0
    p95_ms: float | None = 40.0
    saturation: float | None = 0.2
    alive: bool = True
    sat_label: str = "utilization"
    status: Literal["ok", "warning", "failing", "unknown"] = "ok"
    assessment: Literal["unassessed", "suspect", "ruled_out", "origin"] = "unassessed"
    trend: Trend = Field(default_factory=Trend)
    extras: dict[str, float] = Field(default_factory=dict)
    checks: list[str] = Field(default_factory=list)
    logs_indexed: bool = False


class Edge(BaseModel):
    source: str = Field(alias="from")
    to: str
    kind: Literal["calls", "uses", "publishes", "consumes"] = "calls"
    rps: float | None = 12.0
    errors: float | None = 0.0
    p95_ms: float | None = 20.0
    observed: bool = False


class Source(BaseModel):
    ok: bool = False
    age_secs: float = 0.0


class Sources(BaseModel):
    prometheus: Source = Field(default_factory=Source)
    jaeger: Source = Field(default_factory=Source)
    logstore: Source = Field(default_factory=Source)


class Graph(BaseModel):
    schema_version: Literal["nightwatch.snapshot.v2"] = "nightwatch.snapshot.v2"
    seq: int = 1
    at: str = "2026-09-12T08:00:00+08:00"
    nodes: list[Node]
    edges: list[Edge]
    sources: Sources = Field(default_factory=Sources)
    gap_before: None = None


def dummy_graph(state: Literal["normal", "problem"] = "normal") -> Graph:
    node_ids = [
        "frontend-proxy", "frontend", "product-catalog", "cart", "checkout",
        "payment", "recommendation", "email", "kafka", "fraud-detection",
    ]
    connections = [
        ("frontend-proxy", "frontend", "calls"),
        ("frontend", "product-catalog", "calls"),
        ("frontend", "cart", "calls"),
        ("frontend", "checkout", "calls"),
        ("frontend", "recommendation", "calls"),
        ("checkout", "payment", "calls"),
        ("checkout", "email", "calls"),
        ("checkout", "kafka", "publishes"),
        ("fraud-detection", "kafka", "consumes"),
    ]
    graph = Graph(
        nodes=[Node(id=node_id, kind="queue" if node_id == "kafka" else "service")
               for node_id in node_ids],
        edges=[Edge(**{"from": source, "to": target, "kind": kind})
               for source, target, kind in connections],
    )
    if state == "problem":
        for node in graph.nodes:
            if node.id == "payment":
                node.status = "failing"
                node.assessment = "origin"
                node.errors = 0.8
                node.p95_ms = 3000.0
                node.saturation = 0.95
                node.trend = Trend(errors="rising", latency="rising", saturation="rising")
            elif node.id in {"checkout", "frontend"}:
                node.status = "warning"
                node.assessment = "suspect"
                node.errors = 0.3
                node.p95_ms = 1500.0
                node.trend = Trend(errors="rising", latency="rising")
        for edge in graph.edges:
            if (edge.source, edge.to) in {
                ("checkout", "payment"), ("frontend", "checkout"),
            }:
                edge.errors = 0.8 if edge.to == "payment" else 0.3
                edge.p95_ms = 3000.0 if edge.to == "payment" else 1500.0
    return graph


@asynccontextmanager
async def lifespan(app):
    default_config = Path(__file__).resolve().parents[2] / "guardroom/shop-web.config.json"
    store = GraphStore(os.getenv("GUARDROOM_CONFIG", str(default_config)))
    app.state.graph_store = store
    log_hub.store = store
    logs, cursor = store.read_file()
    store.commit(logs, cursor)
    store.history.save(store.snapshot)
    app.state.last_history_success = monotonic()

    async def follow_logs():
        while True:
            await asyncio.sleep(1)
            try:
                logs, cursor = store.read_file()
                log_hub.publish(store.commit(logs, cursor))
            except Exception:
                # Keep the previous snapshot/cursor so the next tick retries the batch.
                logging.getLogger(__name__).exception("Graph snapshot update failed")

    async def archive_snapshots():
        while True:
            await asyncio.sleep(store.config.history.interval_seconds)
            try:
                store.history.save(store.snapshot)
                app.state.last_history_success = monotonic()
            except Exception:
                logging.getLogger(__name__).exception("Graph history archive failed; retrying next interval")

    tasks = [asyncio.create_task(follow_logs()), asyncio.create_task(archive_snapshots())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        log_hub.store = None


app = FastAPI(
    title="NightWatch Control API",
    version="0.1.0",
    description="Config topology and file-backed monitor graph snapshots.",
    lifespan=lifespan,
)
log_hub = LogHub()
app.include_router(create_log_router(log_hub, include_events=False))


@app.get("/health/ready", summary="Check Guard Room snapshot processing, independent of monitored node health")
async def ready():
    store = app.state.graph_store
    now = monotonic()
    checks = {
        "graph_updates": now - store.last_commit_monotonic <= 10,
        "history_updates": now - app.state.last_history_success <= max(10, 3 * store.config.history.interval_seconds),
    }
    healthy = all(checks.values())
    return JSONResponse({"status": "ok" if healthy else "degraded", "checks": checks},
                        status_code=200 if healthy else 503)


class SnapshotEntry(BaseModel):
    seq: int
    at: str


class SnapshotPage(BaseModel):
    snapshots: list[SnapshotEntry]
    next_before_seq: int | None


@app.get("/api/graph/snapshots", response_model=SnapshotPage, summary="List retained graph snapshots, newest first")
async def list_graph_snapshots(
    limit: int = Query(default=100, ge=1, le=500),
    before_seq: int | None = Query(default=None, ge=1),
):
    return app.state.graph_store.history.list_snapshots(limit, before_seq)


@app.get("/api/graph", response_model=Graph, summary="Get current or historical monitor graph snapshot")
async def get_graph(
    state: Literal["normal", "problem"] | None = Query(
        default=None, description="Optional explicit dummy preview; omitted returns the live snapshot."
    ),
    timestamp: str | None = Query(
        default=None, pattern=TIMESTAMP_PATTERN,
        description="RFC3339 with timezone, e.g. 2026-09-12T13:00:00+08:00. Return the last archived snapshot at or before this time.",
    ),
) -> Graph:
    """Read stored graph content without recomputing historical state."""
    if timestamp is not None:
        if state is not None:
            raise HTTPException(422, detail="timestamp and state cannot be combined")
        try:
            requested = parse_timestamp(timestamp)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        snapshot = app.state.graph_store.history.at_or_before(requested)
        if snapshot is None:
            raise HTTPException(404, detail={"code": "snapshot_not_found", "message": "沒有不晚於指定時間的保留快照"})
        return Graph.model_validate(snapshot)
    return dummy_graph(state) if state is not None else Graph.model_validate(app.state.graph_store.snapshot)


install_frontend(app, graph_provider=dummy_graph, log_hub=log_hub, live=True)
install_investigations(app)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9999)
