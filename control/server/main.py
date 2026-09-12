"""Standalone dummy graph API. All graph measurements are synthetic."""

from typing import Literal

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

from frontend_api import install_frontend


class Trend(BaseModel):
    errors: Literal["rising", "falling", "flat", "na"] = "flat"
    latency: Literal["rising", "falling", "flat", "na"] = "flat"
    saturation: Literal["rising", "falling", "flat", "na"] = "flat"


class Node(BaseModel):
    id: str = Field(min_length=1)
    kind: Literal["service", "queue"] = "service"
    traffic: float = 12.0
    errors: float = 0.0
    p95_ms: float = 40.0
    saturation: float = 0.2
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
    kind: Literal["calls", "publishes", "consumes"] = "calls"
    rps: float = 12.0
    errors: float = 0.0
    p95_ms: float = 20.0
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
    at: str = "2026-09-12T00:00:00Z"
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


app = FastAPI(
    title="NightWatch Control API",
    version="0.1.0",
    description="Dummy graph data for frontend integration; no live monitoring.",
)


@app.get("/api/graph", response_model=Graph, summary="Get dummy graph snapshot")
def get_graph(
    state: Literal["normal", "problem"] = Query(
        default="normal", description="Select normal or problem dummy graph state."
    ),
) -> Graph:
    """Return a synthetic snapshot; state applies only to this request."""
    return dummy_graph(state)


install_frontend(app, graph_provider=dummy_graph)
