"""Standard logging input and JSONL output adapters."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from threading import Lock
import traceback

from .models import MonitorEvent
from .runtime import current_invocation


class JsonlSink:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self._lock = Lock()

    def emit(self, event: MonitorEvent) -> None:
        line = json.dumps(asdict(event), ensure_ascii=False) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line)


class MonitorLogHandler(logging.Handler):
    """Capture invocation records without changing application logger levels."""
    def emit(self, record: logging.LogRecord) -> None:
        invocation = current_invocation.get()
        if not invocation or not invocation.active or not invocation.config.capture_logs:
            return
        if record.levelno < logging.getLevelName(invocation.config.level):
            return
        if getattr(record, "_nightwatch_captured", False):
            return
        record._nightwatch_captured = True
        try:
            invocation.runtime.record_log(
                invocation, level=record.levelname, message=record.getMessage(),
                logger_name=record.name,
                occurred_at=datetime.fromtimestamp(record.created, timezone.utc).isoformat().replace("+00:00", "Z"),
                trace="".join(traceback.format_exception(*record.exc_info)) if record.exc_info else None,
            )
        except Exception:
            invocation.runtime.record_delivery_error()


_handler = MonitorLogHandler()


def install_logging(logger: logging.Logger | None = None) -> None:
    """Attach once; pass a logger explicitly when it has propagate=False."""
    (logger if logger is not None else logging.getLogger()).addHandler(_handler)
