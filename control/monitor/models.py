"""Domain values and output port, independent of logging and filesystem APIs."""
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    name: str | None = None
    description: str = ""
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    capture_logs: bool = True
    monitor_id: str | None = None
    node_ids: tuple[str, ...] = ()
    instance_id: str | None = None

    def __post_init__(self):
        if self.name is not None and not self.name.strip():
            raise ValueError("name must not be blank")
        if self.level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("unsupported log level")
        for field in ("monitor_id", "instance_id"):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field} must be a nonempty string")
        if not isinstance(self.node_ids, (tuple, list)):
            raise ValueError("node_ids must be a tuple or list of strings")
        if any(not isinstance(node, str) or not node.strip() for node in self.node_ids):
            raise ValueError("node_ids must contain nonempty strings")
        if len(set(self.node_ids)) != len(self.node_ids):
            raise ValueError("node_ids must be unique")
        object.__setattr__(self, "node_ids", tuple(self.node_ids))


@dataclass(frozen=True, slots=True)
class MonitorState:
    name: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: float | None = None
    error: str | None = None

    invocation_id: str | None = None
    parent_invocation_id: str | None = None


@dataclass(frozen=True, slots=True)
class MonitorEvent:
    event_id: str
    timestamp: str
    kind: Literal["started", "finished", "exception", "log"]
    node: str
    invocation_id: str
    parent_invocation_id: str | None
    level: str
    message: str
    status: str | None = None
    logger_name: str | None = None
    traceback: str | None = None
    duration_ms: float | None = None
    error: str | None = None
    monitor_id: str | None = None
    node_ids: tuple[str, ...] = ()
    instance_id: str | None = None


class EventSink(Protocol):
    def emit(self, event: MonitorEvent) -> None: ...
