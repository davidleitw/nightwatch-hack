"""Invocation lifecycle and bounded detail storage with injected output ports."""
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from threading import RLock
from time import perf_counter
import traceback
from uuid import uuid4

from .models import EventSink, MonitorConfig, MonitorEvent, MonitorState


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class Invocation:
    runtime: "MonitorRuntime"
    config: MonitorConfig
    name: str
    id: str
    parent_id: str | None
    started_at: str
    clock: float
    active: bool = True


current_invocation: ContextVar[Invocation | None] = ContextVar("monitor_invocation", default=None)
_emitting: ContextVar[bool] = ContextVar("monitor_emitting", default=False)


class MonitorRuntime:
    def __init__(self, sinks: tuple[EventSink, ...] = (), *, max_events: int = 1000):
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self._sinks = list(sinks)
        self._events: deque[MonitorEvent] = deque(maxlen=max_events)
        self._states: dict[str, MonitorState] = {}
        self._configs: dict[str, MonitorConfig] = {}
        self._active: dict[str, MonitorState] = {}
        self._lock = RLock()
        self.sink_errors = 0

    def add_sink(self, sink: EventSink) -> None:
        with self._lock:
            if sink not in self._sinks:
                self._sinks.append(sink)

    def record_delivery_error(self) -> None:
        with self._lock:
            self.sink_errors += 1

    def register(self, name: str, config: MonitorConfig) -> None:
        with self._lock:
            self._configs[name] = config
            self._states.setdefault(name, MonitorState(name=name, status="idle"))

    def get_states(self) -> dict:
        with self._lock:
            return {name: asdict(state) for name, state in self._states.items()}

    def get_detail(self, name: str) -> dict:
        with self._lock:
            return {
                "config": asdict(self._configs[name]),
                "state": asdict(self._states[name]),
                "active_invocations": [asdict(s) for s in self._active.values() if s.name == name],
                "events": [asdict(e) for e in self._events if e.node == name],
                "event_capacity": self._events.maxlen,
                "sink_errors": self.sink_errors,
            }

    def _emit(self, event: MonitorEvent) -> None:
        if _emitting.get():
            return
        token = _emitting.set(True)
        try:
            invocation = current_invocation.get()
            if invocation and invocation.runtime is self and invocation.id == event.invocation_id:
                event = replace(event, monitor_id=invocation.config.monitor_id,
                                node_ids=invocation.config.node_ids,
                                instance_id=invocation.config.instance_id)
            with self._lock:
                self._events.append(event)
                sinks = tuple(self._sinks)
            for sink in sinks:
                try:
                    sink.emit(event)
                except Exception:
                    self.record_delivery_error()
        finally:
            _emitting.reset(token)

    def record_log(self, invocation: Invocation, *, level: str, message: str,
                   logger_name: str, trace: str | None = None,
                   occurred_at: str | None = None) -> None:
        if invocation.active:
            self._emit(MonitorEvent(
                event_id=uuid4().hex, timestamp=occurred_at or now(), kind="log", node=invocation.name,
                invocation_id=invocation.id, parent_invocation_id=invocation.parent_id,
                level=level, message=message, logger_name=logger_name, traceback=trace,
            ))

    @contextmanager
    def invocation(self, name: str, config: MonitorConfig):
        parent = current_invocation.get()
        invocation = Invocation(self, config, name, uuid4().hex,
                                parent.id if parent and parent.active else None, now(), perf_counter())
        token = current_invocation.set(invocation)
        state = MonitorState(name=name, status="running", started_at=invocation.started_at,
                             invocation_id=invocation.id, parent_invocation_id=invocation.parent_id)
        with self._lock:
            self._states[name] = state
            self._active[invocation.id] = state
        self._emit(MonitorEvent(uuid4().hex, now(), "started", name, invocation.id,
                                invocation.parent_id, "INFO", "node started", status="running"))
        error = None
        trace = None
        status = "ok"
        try:
            yield
        except BaseException as exc:
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
            trace = "".join(traceback.format_exception(exc))
            raise
        finally:
            duration = round((perf_counter() - invocation.clock) * 1000, 3)
            state = MonitorState(name, status, invocation.started_at, now(), duration,
                                 error, invocation.id, invocation.parent_id)
            with self._lock:
                self._active.pop(invocation.id, None)
                self._states[name] = state
            self._emit(MonitorEvent(
                uuid4().hex, state.finished_at, "exception" if error else "finished",
                name, invocation.id, invocation.parent_id, "ERROR" if error else "INFO",
                "node failed" if error else "node finished", status=status,
                traceback=trace, duration_ms=duration, error=error,
            ))
            invocation.active = False
            current_invocation.reset(token)
