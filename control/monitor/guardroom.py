"""Bounded background HTTP output adapter; no network work in emit()."""
from dataclasses import dataclass
import json
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .console import to_console_log
from .models import MonitorEvent


@dataclass(frozen=True)
class GuardRoomSinkConfig:
    endpoint: str = "http://127.0.0.1:9999/api/logs"
    batch_size: int = 50
    flush_interval_seconds: float = 0.5
    queue_capacity: int = 1000
    timeout_seconds: float = 3
    max_retries: int = 3
    retry_delay_seconds: float = 0.25
    max_event_bytes: int = 262144

    def __post_init__(self):
        url = urlsplit(self.endpoint)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("endpoint must be an HTTP(S) URL without embedded credentials")
        for name in ("batch_size", "queue_capacity", "max_event_bytes"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.batch_size > 50:
            raise ValueError("batch_size must not exceed the receiver limit of 50")
        if type(self.max_retries) is not int or self.max_retries < 0:
            raise ValueError("max_retries must be a nonnegative integer")
        for name in ("flush_interval_seconds", "timeout_seconds", "retry_delay_seconds"):
            value = getattr(self, name)
            if not 0 < value < float("inf"):
                raise ValueError(f"{name} must be finite and positive")


class GuardRoomSink:
    def __init__(self, config: GuardRoomSinkConfig | None = None):
        self.config = config or GuardRoomSinkConfig()
        self._queue = Queue(maxsize=self.config.queue_capacity)
        self._lock = Lock()
        self._wake = Event()
        self._abort = Event()
        self._thread = None
        self._closed = False
        self._stats = dict(sent=0, retried=0, dropped=0, in_flight=0, last_error=None)

    def start(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("sink is closed")
            if self._thread is None:
                self._thread = Thread(target=self._run, name="guardroom-sink", daemon=True)
                self._thread.start()
        return self

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()

    def stats(self) -> dict:
        with self._lock:
            return {**self._stats, "queued": self._queue.qsize(), "closed": self._closed}

    def emit(self, event: MonitorEvent) -> None:
        payload = to_console_log(event)
        # Queue immutable serialized payloads; retries send exactly the same IDs/body.
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        with self._lock:
            if self._closed or self._thread is None:
                self._stats["dropped"] += 1
                self._stats["last_error"] = "sink is not running"
                return
            if len(encoded) > self.config.max_event_bytes:
                self._stats["dropped"] += 1
                self._stats["last_error"] = "event exceeds max_event_bytes"
                return
            try:
                self._queue.put_nowait(encoded)
            except Full:
                self._stats["dropped"] += 1
                self._stats["last_error"] = "queue is full"
                return
        self._wake.set()

    def close(self, timeout: float = 5) -> bool:
        if timeout < 0:
            raise ValueError("timeout must be nonnegative")
        with self._lock:
            self._closed = True
            thread = self._thread
        self._wake.set()
        if thread is None:
            return True
        thread.join(timeout)
        if thread.is_alive():
            self._abort.set()
            self._wake.set()
            return False
        return True

    def _run(self):
        while not self._abort.is_set():
            try:
                first = self._queue.get_nowait()
            except Empty:
                if self._closed:
                    return
                self._wake.wait(self.config.flush_interval_seconds)
                self._wake.clear()
                continue
            batch = [first]
            deadline = monotonic() + self.config.flush_interval_seconds
            while len(batch) < self.config.batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except Empty:
                    remaining = deadline - monotonic()
                    if self._closed or self._abort.is_set() or remaining <= 0:
                        break
                    self._wake.wait(remaining)
                    self._wake.clear()
            with self._lock:
                self._stats["in_flight"] = len(batch)
            success = self._send(batch)
            with self._lock:
                self._stats["sent" if success else "dropped"] += len(batch)
                self._stats["in_flight"] = 0
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                return
            with self._lock:
                self._stats["dropped"] += 1

    def _send(self, batch) -> bool:
        body = b'{"logs":[' + b','.join(batch) + b']}'
        for attempt in range(self.config.max_retries + 1):
            if self._abort.is_set():
                return False
            retry = False
            try:
                request = Request(self.config.endpoint, data=body,
                                  headers={"Content-Type": "application/json"}, method="POST")
                with urlopen(request, timeout=self.config.timeout_seconds) as response:
                    if 200 <= response.status < 300:
                        return True
                    error = f"HTTP {response.status}"
            except HTTPError as exc:
                error = f"HTTP {exc.code}"
                retry = exc.code == 429 or 500 <= exc.code < 600
                exc.close()
            except (URLError, OSError) as exc:
                error = f"{type(exc).__name__}: {exc}"
                retry = True
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            with self._lock:
                self._stats["last_error"] = error
            if not retry or attempt == self.config.max_retries:
                return False
            if self._abort.wait(min(self.config.retry_delay_seconds * 2 ** attempt, 5)):
                return False
            with self._lock:
                self._stats["retried"] += 1
        return False
