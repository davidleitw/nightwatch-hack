"""Conservative monitor-graph detection; durable latches belong to the store."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any


class GraphDetector:
    """Require three consecutive fresh observations of the same node.

    Counters intentionally restart with the process. Missing, repeated, old or
    out-of-order observations break confirmation, but never clear a durable latch.
    """

    def __init__(self):
        self._last_seq: int | None = None
        self._last_at: datetime | None = None
        self._candidates: dict[str, list[dict[str, Any]]] = {}
        self._healthy = 0

    def reset(self) -> None:
        self._candidates.clear()
        self._healthy = 0

    def observe(self, graph: dict[str, Any], latched_nodes: list[str] | None = None):
        at = datetime.fromisoformat(graph["at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - at).total_seconds()
        seq = graph["seq"]
        advancing = (self._last_seq is None or seq > self._last_seq) and (
            self._last_at is None or at > self._last_at)
        continuous = self._last_at is None or 0 < (at - self._last_at).total_seconds() <= 15
        self._last_seq, self._last_at = seq, at
        if not advancing or not -5 <= age <= 15 or not graph["sources"]["logstore"]["ok"]:
            self.reset()
            return False, None, False
        if not continuous:
            self.reset()

        nodes = {node["id"]: node for node in graph["nodes"]}
        abnormal = {key: node for key, node in nodes.items() if node["status"] in {"warning", "failing"}}
        if latched_nodes is not None:
            healthy = bool(latched_nodes) and not abnormal and all(
                key in nodes and nodes[key]["status"] == "ok" for key in latched_nodes)
            self._healthy = self._healthy + 1 if healthy else 0
            self._candidates.clear()
            return True, None, self._healthy >= 3

        self._healthy = 0
        self._candidates = {
            key: (self._candidates.get(key, []) + [{
                "seq": seq, "at": graph["at"], "status": node["status"],
                "errors": node["errors"], "p95_ms": node["p95_ms"],
            }])[-3:]
            for key, node in abnormal.items()
        }
        confirmed = sorted(key for key, rows in self._candidates.items() if len(rows) == 3)
        if not confirmed:
            return True, None, False
        reason = "連續三次新快照觀測到節點異常：" + "、".join(
            f"{key} ({nodes[key]['status']}, errors={nodes[key]['errors']}, p95_ms={nodes[key]['p95_ms']})"
            for key in confirmed)
        return True, {
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason, "node_ids": confirmed,
            "confirmations": {key: copy.deepcopy(self._candidates[key]) for key in confirmed},
            "snapshot": copy.deepcopy(graph),
        }, False
