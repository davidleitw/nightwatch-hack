"""In-memory demo fault controls used by the shop checkout API.

The manager deliberately owns no database connection in the application
thread.  The database-write-lock fault uses one short-lived worker thread
whose SQLite connection is created, used, and closed in that same thread.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import monotonic

from .logging_config import logger


FAULT_IDS = (
    "checkout_exception",
    "database_write_lock",
    "checkout_delay",
)
FAULT_TTL_SECONDS = 60.0
CHECKOUT_DELAY_SECONDS = 10.0
DB_LOCK_ACQUIRE_TIMEOUT_SECONDS = 1.0
_POLL_INTERVAL_SECONDS = 0.05

FAULT_CARDS = (
    {
        "fault_id": "checkout_exception",
        "title": "結帳例外",
        "description": "在結帳交易寫入後觸發例外，驗證 transaction rollback。",
    },
    {
        "fault_id": "database_write_lock",
        "title": "資料庫寫入鎖",
        "description": "由專用 SQLite connection 持有寫入鎖，驗證真實 lock timeout。",
    },
    {
        "fault_id": "checkout_delay",
        "title": "結帳延遲",
        "description": "結帳請求非阻塞等待 10 秒，解除故障可立即喚醒。",
    },
)


class DemoFaultConflictError(Exception):
    """Raised when another fault is already active or being cleaned up."""


class DemoFaultUnavailableError(Exception):
    """Raised when the SQLite lock cannot be acquired in time."""


@dataclass
class _DatabaseLockWorker:
    """A dedicated SQLite lock owner with thread-local connection lifetime."""

    ready: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    cancel: threading.Event = field(default_factory=threading.Event)
    acquired: bool = False
    error: BaseException | None = None
    thread: threading.Thread | None = None

    def start(self, path: str) -> None:
        self.thread = threading.Thread(
            target=self._run,
            args=(path,),
            name="shop-demo-database-lock",
            daemon=True,
        )
        self.thread.start()

    def _run(self, path: str) -> None:
        db: sqlite3.Connection | None = None
        try:
            db = sqlite3.connect(
                path,
                timeout=DB_LOCK_ACQUIRE_TIMEOUT_SECONDS,
            )
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("BEGIN IMMEDIATE")
            self.acquired = True
        except BaseException as exc:
            self.error = exc
        finally:
            # The activation coroutine must observe the result before it can
            # report the fault as active.
            self.ready.set()

        try:
            if self.acquired:
                self.cancel.wait()
        finally:
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    # Closing the connection is still attempted below.  A
                    # rollback failure cannot leave this worker unreported.
                    pass
                try:
                    db.close()
                except BaseException as exc:
                    if self.error is None:
                        self.error = exc
                finally:
                    self.done.set()
            else:
                self.done.set()


@dataclass
class _FaultState:
    fault_id: str
    started_at: str
    expires_monotonic: float | None = None
    expires_at: str | None = None
    active: bool = False
    pending: bool = False
    cleanup_pending: bool = False
    wake_event: asyncio.Event | None = None
    database_lock: _DatabaseLockWorker | None = None


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _new_lease() -> tuple[str, float, str]:
    now = datetime.now(timezone.utc)
    return (
        now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        monotonic() + FAULT_TTL_SECONDS,
        (now + timedelta(seconds=FAULT_TTL_SECONDS))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
    )


class DemoFaultManager:
    """Coordinate one process-local demo fault and its cleanup lifecycle."""

    def __init__(self) -> None:
        self._state_lock = threading.RLock()
        self._operation_lock = asyncio.Lock()
        self._current: _FaultState | None = None
        self._expiry_task: asyncio.Task[None] | None = None
        self._shutdown_event: asyncio.Event | None = None

    async def start(self) -> None:
        async with self._operation_lock:
            if self._expiry_task is not None and not self._expiry_task.done():
                return
            self._shutdown_event = asyncio.Event()
            self._expiry_task = asyncio.create_task(
                self._expiry_loop(),
                name="shop-demo-fault-expiry",
            )
            logger.info("demo fault manager started")

    async def shutdown(self) -> None:
        task: asyncio.Task[None] | None
        async with self._operation_lock:
            current = self._current
            if current is not None:
                await self._cleanup_current_locked(current, reason="shutdown")
            shutdown_event = self._shutdown_event
            if shutdown_event is not None:
                shutdown_event.set()
            task = self._expiry_task
            self._expiry_task = None
            self._shutdown_event = None

        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info("demo fault manager stopped")

    async def get_state(self) -> dict:
        async with self._operation_lock:
            await self._cleanup_expired_locked()
            return self._response()

    async def activate(self, fault_id: str) -> dict:
        if fault_id not in FAULT_IDS:
            raise ValueError(f"unknown demo fault id: {fault_id}")

        async with self._operation_lock:
            await self._cleanup_expired_locked()
            with self._state_lock:
                if self._current is not None:
                    raise DemoFaultConflictError(
                        "another demo fault is active or being cleaned up"
                    )
                state = _FaultState(
                    fault_id=fault_id,
                    started_at=_utc_timestamp(),
                )
                if fault_id == "checkout_delay":
                    state.wake_event = asyncio.Event()
                self._current = state

            if fault_id == "database_write_lock":
                await self._activate_database_lock_locked(state)
            else:
                with self._state_lock:
                    started_at, expires_monotonic, expires_at = _new_lease()
                    state.started_at = started_at
                    state.expires_monotonic = expires_monotonic
                    state.expires_at = expires_at
                    state.active = True
                logger.info(
                    "demo fault activated fault_id=%s expires_at=%s",
                    fault_id,
                    state.expires_at,
                )
            return self._response()

    async def deactivate(self) -> dict:
        async with self._operation_lock:
            await self._cleanup_expired_locked()
            with self._state_lock:
                current = self._current
            if current is not None:
                await self._cleanup_current_locked(current, reason="manual")
            return self._response()

    def checkout_fault_id(self) -> str | None:
        """Read the active checkout fault without touching the event loop."""
        with self._state_lock:
            current = self._current
            if (
                current is None
                or not current.active
                or current.cleanup_pending
                or current.expires_monotonic is None
                or current.expires_monotonic <= monotonic()
            ):
                return None
            return current.fault_id

    async def wait_for_checkout_delay(self) -> str | None:
        """Wait without blocking the event loop and wake on clear/expiry."""
        with self._state_lock:
            current = self._current
            if (
                current is None
                or not current.active
                or current.fault_id != "checkout_delay"
                or current.wake_event is None
            ):
                return None
            fault_id = current.fault_id
            wake_event = current.wake_event
            remaining = min(
                CHECKOUT_DELAY_SECONDS,
                max(
                    0.0,
                    (current.expires_monotonic or monotonic()) - monotonic(),
                ),
            )

        try:
            await asyncio.wait_for(wake_event.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            async with self._operation_lock:
                await self._cleanup_expired_locked()
        return fault_id

    async def _activate_database_lock_locked(self, state: _FaultState) -> None:
        worker = _DatabaseLockWorker()
        with self._state_lock:
            state.pending = True
            state.database_lock = worker

        try:
            worker.start(str(self._database_path()))
        except BaseException as exc:
            worker.error = exc
            worker.done.set()
            await self._cleanup_current_locked(state, reason="activation_error")
            logger.error(
                "demo fault activation failed fault_id=%s reason=database_lock_start",
                state.fault_id,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            raise DemoFaultUnavailableError(
                "database write lock could not be started"
            ) from exc

        ready = await self._wait_for_thread_event(
            worker.ready,
            DB_LOCK_ACQUIRE_TIMEOUT_SECONDS + 1.0,
        )
        if not ready or not worker.acquired:
            await self._cleanup_current_locked(state, reason="activation_failed")
            error = worker.error
            if error is not None:
                logger.error(
                    "demo fault activation failed fault_id=%s reason=database_lock_acquire",
                    state.fault_id,
                    exc_info=(type(error), error, error.__traceback__),
                )
            else:
                logger.error(
                    "demo fault activation failed fault_id=%s reason=database_lock_timeout",
                    state.fault_id,
                )
            raise DemoFaultUnavailableError(
                "database write lock could not be acquired"
            )

        with self._state_lock:
            cancelled = state.cleanup_pending or self._current is not state
            if not cancelled:
                started_at, expires_monotonic, expires_at = _new_lease()
                state.started_at = started_at
                state.expires_monotonic = expires_monotonic
                state.expires_at = expires_at
                state.pending = False
                state.active = True

        if cancelled:
            await self._cleanup_current_locked(state, reason="activation_cancelled")
            raise DemoFaultUnavailableError("database write lock activation cancelled")

        logger.info(
            "demo fault activated fault_id=%s expires_at=%s",
            state.fault_id,
            state.expires_at,
        )

    async def _cleanup_expired_locked(self) -> None:
        with self._state_lock:
            current = self._current
            expired = (
                current is not None
                and current.expires_monotonic is not None
                and current.expires_monotonic <= monotonic()
            )
        if expired and current is not None:
            await self._cleanup_current_locked(current, reason="ttl")

    async def _cleanup_current_locked(
        self,
        state: _FaultState,
        *,
        reason: str,
    ) -> None:
        with self._state_lock:
            if self._current is not state:
                return
            state.cleanup_pending = True
            state.active = False
            state.pending = False
            if state.wake_event is not None:
                state.wake_event.set()
            worker = state.database_lock
            if worker is not None:
                worker.cancel.set()

        if worker is not None:
            # The worker's SQLite timeout is finite.  Polling here keeps the
            # event loop responsive while guaranteeing that no later
            # activation can race a still-open connection.
            await self._wait_for_thread_event(worker.done)

        with self._state_lock:
            if self._current is state:
                self._current = None
        logger.info(
            "demo fault deactivated fault_id=%s reason=%s",
            state.fault_id,
            reason,
        )

    async def _expiry_loop(self) -> None:
        shutdown_event = self._shutdown_event
        if shutdown_event is None:
            return
        try:
            while not shutdown_event.is_set():
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=_POLL_INTERVAL_SECONDS,
                    )
                except asyncio.TimeoutError:
                    pass
                if shutdown_event.is_set():
                    return
                async with self._operation_lock:
                    await self._cleanup_expired_locked()
        except asyncio.CancelledError:
            raise

    async def _wait_for_thread_event(
        self,
        event: threading.Event,
        timeout: float | None = None,
    ) -> bool:
        deadline = monotonic() + timeout if timeout is not None else None
        while not event.is_set():
            if deadline is None:
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                continue
            remaining = deadline - monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(_POLL_INTERVAL_SECONDS, remaining))
        return True

    @staticmethod
    def _database_path():
        # Import lazily to avoid importing app.main while app.main imports this
        # module.  The call happens only after the app's migration completed.
        from .main import database_path

        return database_path()

    def _response(self) -> dict:
        with self._state_lock:
            current = self._current
            active = None
            if current is not None:
                if current.active:
                    status = "active"
                elif current.pending:
                    status = "activating"
                elif current.cleanup_pending:
                    status = "restoring"
                else:
                    status = None

                if status is not None:
                    remaining_seconds = (
                        max(0.0, current.expires_monotonic - monotonic())
                        if current.expires_monotonic is not None
                        else 0.0
                    )
                    active = {
                        "fault_id": current.fault_id,
                        "started_at": current.started_at,
                        "expires_at": current.expires_at,
                        "remaining_seconds": round(remaining_seconds, 3),
                        "status": status,
                    }

        return {
            "cards": [dict(card) for card in FAULT_CARDS],
            "active": active,
            "lease_seconds": int(FAULT_TTL_SECONDS),
            "delay_seconds": int(CHECKOUT_DELAY_SECONDS),
        }


demo_fault_manager = DemoFaultManager()
