"""Public decorator API and default dependency wiring."""
from dataclasses import replace
from functools import wraps
import inspect
from pathlib import Path
from threading import Lock

from .adapters import JsonlSink, install_logging
from .models import EventSink, MonitorConfig, MonitorEvent, MonitorState
from .runtime import MonitorRuntime, get_invocation_id, invocation_context
from .console import ConsoleLogSink, to_console_log
from .guardroom import GuardRoomSink, GuardRoomSinkConfig

DEFAULT_SINK_PATH = Path(__file__).resolve().parents[1] / "tmp" / "monitor.jsonl"
_default_runtime = MonitorRuntime((JsonlSink(DEFAULT_SINK_PATH),))
_sink_paths = {DEFAULT_SINK_PATH.resolve()}
_sink_lock = Lock()


def configure_file_sink(path: str | Path = DEFAULT_SINK_PATH) -> Path:
    """Add an idempotent JSONL destination to the default runtime."""
    path = Path(path).resolve()
    with _sink_lock:
        if path not in _sink_paths:
            _default_runtime.add_sink(JsonlSink(path))
            _sink_paths.add(path)
    return path


def get_states() -> dict:
    return _default_runtime.get_states()


def get_detail(name: str) -> dict:
    return _default_runtime.get_detail(name)


def monitor(function=None, *, config: MonitorConfig | None = None,
            name: str | None = None, runtime: MonitorRuntime | None = None,
            exception_is_error=None, result_error=None):
    """Use @monitor, @monitor(), or @monitor(MonitorConfig(...))."""
    if isinstance(function, MonitorConfig):
        if config is not None:
            raise TypeError("config supplied twice")
        config, function = function, None
    if config is not None and not isinstance(config, MonitorConfig):
        raise TypeError("config must be a MonitorConfig")
    for classifier in (exception_is_error, result_error):
        if classifier is not None and not callable(classifier):
            raise TypeError("monitor classifiers must be callable")
    config = config or MonitorConfig()
    if name is not None:
        config = replace(config, name=name)
    selected_runtime = runtime if runtime is not None else _default_runtime

    def decorate(target):
        if inspect.isgeneratorfunction(target) or inspect.isasyncgenfunction(target):
            raise TypeError("monitor supports regular and async functions, not generators")
        node_name = config.name or target.__qualname__
        effective_config = replace(
            config, name=node_name,
            monitor_id=config.monitor_id or f"{target.__module__}.{target.__qualname__}",
        )
        selected_runtime.register(node_name, effective_config)
        install_logging()
        if inspect.iscoroutinefunction(target):
            @wraps(target)
            async def async_wrapper(*args, **kwargs):
                with selected_runtime.invocation(node_name, effective_config,
                                                 exception_is_error=exception_is_error) as invocation:
                    result = await target(*args, **kwargs)
                    if result_error is not None:
                        invocation.result_error = result_error(result)
                    return result
            return async_wrapper

        @wraps(target)
        def wrapper(*args, **kwargs):
            with selected_runtime.invocation(node_name, effective_config,
                                             exception_is_error=exception_is_error) as invocation:
                result = target(*args, **kwargs)
                if result_error is not None:
                    invocation.result_error = result_error(result)
                return result
        return wrapper

    return decorate(function) if function is not None else decorate


__all__ = [
    "DEFAULT_SINK_PATH", "EventSink", "JsonlSink", "MonitorConfig", "MonitorEvent",
    "MonitorRuntime", "MonitorState", "configure_file_sink", "get_detail", "get_states",
    "install_logging", "monitor", "get_invocation_id", "invocation_context",
    "ConsoleLogSink", "to_console_log",
    "GuardRoomSink", "GuardRoomSinkConfig",
]
