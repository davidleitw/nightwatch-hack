"""Projection to the console log contract, independent of transport."""
from collections.abc import Callable

from .models import MonitorEvent

_LEVELS = {
    "TRACE": "TRACE", "DEBUG": "DEBUG", "INFO": "INFO",
    "WARNING": "WARN", "WARN": "WARN", "ERROR": "ERROR",
    "CRITICAL": "FATAL", "FATAL": "FATAL",
}


def to_console_log(event: MonitorEvent) -> dict:
    """Preserve source identity/time on replay; never guess graph membership."""
    if not event.monitor_id:
        raise ValueError("console projection requires monitor_id")
    if event.level not in _LEVELS:
        raise ValueError(f"unsupported console log level: {event.level}")
    attributes = {"kind": event.kind, "node": event.node}
    for field in ("status", "logger_name", "traceback", "duration_ms", "error", "parent_invocation_id"):
        value = getattr(event, field)
        if value is not None:
            attributes[field] = value
    payload = {
        "schema_version": "nightwatch.log.v1",
        "event_id": event.event_id,
        "monitor_id": event.monitor_id,
        "occurred_at": event.timestamp,
        "level": _LEVELS[event.level],
        "message": event.message,
        "refs": {"node_ids": list(event.node_ids)},
        "invocation_id": event.invocation_id,
        "attributes": attributes,
    }
    if event.instance_id is not None:
        payload["instance_id"] = event.instance_id
    return payload


class ConsoleLogSink:
    """Adapt domain events to a synchronous payload writer (queue, file, etc.)."""
    def __init__(self, write: Callable[[dict], None]):
        self._write = write

    def emit(self, event: MonitorEvent) -> None:
        self._write(to_console_log(event))
