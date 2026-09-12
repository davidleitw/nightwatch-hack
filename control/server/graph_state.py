"""Config topology + monitor logs, with one atomic file checkpoint (single worker)."""
from collections import OrderedDict, defaultdict
from datetime import datetime
import json
import logging
import math
import os
from pathlib import Path
from time import monotonic
from typing import Literal

from pydantic import Field, model_validator

from logs import ConsoleLog, Identifier, StrictModel
from graph_history import GraphHistory, HistoryConfig, TAIPEI, write_json_atomic

LOG = logging.getLogger(__name__)
CAPACITY = 10000


class LatencyConfig(StrictModel):
    warning_ms: float = Field(gt=0, allow_inf_nan=False)
    min_samples: int = Field(ge=1)


class MonitorDefinition(StrictModel):
    monitor_id: Identifier
    node_id: Identifier
    kind: Literal["service", "datastore", "queue", "volume", "synthetic", "external"] = "service"
    latency: LatencyConfig | None = None


class Connection(StrictModel):
    source: Identifier = Field(alias="from")
    to: Identifier
    kind: Literal["calls", "uses", "publishes", "consumes"] = "calls"


class GraphConfig(StrictModel):
    version: Literal[1]
    monitor_log: Identifier
    snapshot_path: Identifier
    window_seconds: int = Field(default=60, ge=1)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    monitors: list[MonitorDefinition] = Field(min_length=1)
    edges: list[Connection]

    @model_validator(mode="after")
    def topology(self):
        ids = {m.monitor_id for m in self.monitors}
        if len(ids) != len(self.monitors):
            raise ValueError("monitor_id must be unique")
        if len({m.node_id for m in self.monitors}) != len(self.monitors):
            raise ValueError("node_id must be unique")
        edges = set()
        for edge in self.edges:
            if edge.source not in ids or edge.to not in ids:
                raise ValueError("edge endpoints must reference configured monitor_id values")
            key = (edge.source, edge.to, edge.kind)
            if key in edges:
                raise ValueError("duplicate edge")
            edges.add(key)
        return self


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def console_payload(raw):
    """Accept console JSONL and the current monitor's native JSONL format."""
    if raw.get("schema_version") == "nightwatch.log.v1":
        return ConsoleLog.model_validate(raw).model_dump(exclude_none=True)
    payload = {
        "schema_version": "nightwatch.log.v1",
        "event_id": raw["event_id"], "monitor_id": raw["monitor_id"],
        "occurred_at": raw["timestamp"],
        "level": {"WARNING": "WARN", "CRITICAL": "FATAL"}.get(raw["level"], raw["level"]),
        "message": raw["message"], "refs": {"node_ids": raw.get("node_ids", [])},
        "attributes": {key: raw[key] for key in (
            "kind", "status", "duration_ms", "parent_invocation_id", "error", "node"
        ) if raw.get(key) is not None},
    }
    for key in ("invocation_id", "instance_id"):
        if raw.get(key) is not None:
            payload[key] = raw[key]
    return ConsoleLog.model_validate(payload).model_dump(exclude_none=True)


class GraphStore:
    def __init__(self, config_path):
        config_path = Path(config_path).resolve()
        self.config = GraphConfig.model_validate_json(config_path.read_text())
        self.log_path = (config_path.parent / self.config.monitor_log).resolve()
        self.path = (config_path.parent / self.config.snapshot_path).resolve()
        if self.path in (self.log_path, config_path):
            raise ValueError("snapshot_path must differ from config and monitor_log")
        history_path = (config_path.parent / self.config.history.directory).resolve()
        if any(path == history_path or history_path in path.parents
               for path in (self.path, self.log_path, config_path)):
            raise ValueError("history directory must be separate from config, checkpoint and monitor log")
        self.history = GraphHistory(history_path, self.config.history)
        self.monitors = {m.monitor_id: m for m in self.config.monitors}
        self.recent = OrderedDict()
        self.cursor = {"path": str(self.log_path), "identity": None, "offset": 0}
        self.seq = self.history.max_seq
        self.snapshot = None
        if self.path.exists():
            saved = json.loads(self.path.read_text())
            if saved["version"] != 1:
                raise ValueError("unsupported graph checkpoint version")
            self.seq = max(self.seq, saved["snapshot"]["seq"])
            for raw in saved["recent_logs"][-CAPACITY:]:
                payload = console_payload(raw)
                self.recent[(payload["monitor_id"], payload["event_id"])] = payload
            if saved["file_cursor"]["path"] == str(self.log_path):
                self.cursor = saved["file_cursor"]
        self.commit([], self.cursor)

    def project(self, recent, seq):
        now = datetime.now(TAIPEI)
        cutoff = now.timestamp() - self.config.window_seconds
        nodes = []
        by_monitor = defaultdict(list)
        for log in recent.values():
            by_monitor[log["monitor_id"]].append(log)
        for monitor in self.config.monitors:
            logs = by_monitor[monitor.monitor_id]
            active = [log for log in logs if cutoff <= timestamp(log["occurred_at"]) <= now.timestamp()]
            completed = [log for log in active if log.get("attributes", {}).get("kind") in ("finished", "exception")]
            failed = sum(log["attributes"].get("status") == "error" or log["attributes"]["kind"] == "exception" for log in completed)
            durations = sorted(value for log in completed
                               if type(value := log["attributes"].get("duration_ms")) in (int, float)
                               and math.isfinite(value) and value >= 0)
            p95_ms = durations[math.ceil(len(durations) * .95) - 1] if durations else None
            status = "unknown"
            if completed:
                status = "failing" if failed == len(completed) else "warning" if failed else "ok"
            if (status == "ok" and monitor.latency is not None and p95_ms is not None
                    and len(durations) >= monitor.latency.min_samples
                    and p95_ms >= monitor.latency.warning_ms):
                status = "warning"
            nodes.append({
                "id": monitor.node_id, "kind": monitor.kind,
                "traffic": len(completed) / self.config.window_seconds if active else None,
                "errors": failed / len(completed) if completed else None,
                "p95_ms": p95_ms,
                "saturation": None, "alive": bool(active), "sat_label": "utilization",
                "status": status, "assessment": "unassessed",
                "trend": {"errors": "na", "latency": "na", "saturation": "na"},
                "extras": {}, "checks": [], "logs_indexed": bool(logs),
            })
        known_logs = [log for log in recent.values() if log["monitor_id"] in self.monitors]
        age = max(0, now.timestamp() - max(timestamp(log["occurred_at"]) for log in known_logs)) if known_logs else 0
        return {
            "schema_version": "nightwatch.snapshot.v2", "seq": seq,
            "at": now.isoformat(), "nodes": nodes,
            "edges": [{"from": self.monitors[e.source].node_id, "to": self.monitors[e.to].node_id,
                       "kind": e.kind, "rps": None, "errors": None, "p95_ms": None,
                       "observed": False} for e in self.config.edges],
            "sources": {"prometheus": {"ok": False, "age_secs": 0},
                        "jaeger": {"ok": False, "age_secs": 0},
                        "logstore": {"ok": bool(known_logs) and age <= self.config.window_seconds, "age_secs": age}},
            "gap_before": None,
        }

    def commit(self, logs, cursor=None):
        recent = self.recent.copy()
        fresh = []
        for payload in logs:
            key = (payload["monitor_id"], payload["event_id"])
            if key in recent:
                continue
            # Config owns graph membership, including the refs sent to SSE clients.
            monitor = self.monitors.get(payload["monitor_id"])
            payload = {**payload, "refs": {"node_ids": [monitor.node_id] if monitor else []}}
            recent[key] = payload
            fresh.append(payload)
            if len(recent) > CAPACITY:
                recent.popitem(last=False)
        snapshot = self.project(recent, self.seq + 1)
        cursor = cursor if cursor is not None else self.cursor
        saved = {"version": 1, "snapshot": snapshot, "recent_logs": list(recent.values()), "file_cursor": cursor}
        write_json_atomic(self.path, saved)
        self.recent, self.cursor, self.snapshot = recent, cursor, snapshot
        self.seq = snapshot["seq"]
        self.last_commit_monotonic = monotonic()
        return fresh

    def read_file(self):
        """Read complete lines only. Persist cursor together with their graph effects."""
        try:
            stream = self.log_path.open("rb")
        except FileNotFoundError:
            return [], self.cursor
        logs = []
        with stream:
            stat = os.fstat(stream.fileno())
            identity = [stat.st_dev, stat.st_ino]
            offset = self.cursor["offset"]
            if identity != self.cursor["identity"] or stat.st_size < offset:
                offset = 0
            stream.seek(offset)
            skipped = 0
            for _ in range(1000):
                line = stream.readline()
                if not line.endswith(b"\n"):
                    break
                offset = stream.tell()
                try:
                    logs.append(console_payload(json.loads(line)))
                except (ValueError, KeyError, TypeError, AttributeError):
                    skipped += 1
            if skipped:
                LOG.warning("Skipped %s legacy or invalid monitor log records in %s", skipped, self.log_path)
        return logs, {"path": str(self.log_path), "identity": identity, "offset": offset}
