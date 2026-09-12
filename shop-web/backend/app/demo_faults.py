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
from datetime import datetime, timezone

from .logging_config import logger


FAULT_IDS = (
    "checkout_exception",
    "database_write_lock",
    "checkout_delay",
)
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
    active: bool = False
    pending: bool = False
    cleanup_pending: bool = False
    wake_event: asyncio.Event | None = None
    database_lock: _DatabaseLockWorker | None = None


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


class DemoFaultManager:
    """Coordinate one process-local demo fault and its cleanup lifecycle."""

    def __init__(self, database_path_provider=None) -> None:
        self._state_lock = threading.RLock()
        self._operation_lock = asyncio.Lock()
        self._database_path_provider = database_path_provider
        self._current: _FaultState | None = None

    async def start(self) -> None:
        async with self._operation_lock:
            logger.info("demo fault manager started")

    async def shutdown(self) -> None:
        async with self._operation_lock:
            current = self._current
            if current is not None:
                await self._cleanup_current_locked(current, reason="shutdown")
        logger.info("demo fault manager stopped")

    async def get_state(self) -> dict:
        async with self._operation_lock:
            return self._response()

    async def activate(self, fault_id: str) -> dict:
        if fault_id not in FAULT_IDS:
            raise ValueError(f"unknown demo fault id: {fault_id}")

        async with self._operation_lock:
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
                    state.active = True
                logger.info("demo fault activated fault_id=%s", fault_id)
            return self._response()

    async def deactivate(self) -> dict:
        async with self._operation_lock:
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
            ):
                return None
            return current.fault_id

    async def wait_for_checkout_delay(self) -> str | None:
        """Wait without blocking the event loop and wake on manual clear."""
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

        try:
            await asyncio.wait_for(
                wake_event.wait(),
                timeout=CHECKOUT_DELAY_SECONDS,
            )
        except asyncio.TimeoutError:
            pass
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
                state.pending = False
                state.active = True

        if cancelled:
            await self._cleanup_current_locked(state, reason="activation_cancelled")
            raise DemoFaultUnavailableError("database write lock activation cancelled")

        logger.info("demo fault activated fault_id=%s", state.fault_id)

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

    async def _wait_for_thread_event(
        self,
        event: threading.Event,
        timeout: float | None = None,
    ) -> bool:
        async def poll() -> None:
            while not event.is_set():
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)

        try:
            if timeout is None:
                await poll()
            else:
                await asyncio.wait_for(poll(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return True

    def _database_path(self):
        # Order service instances inject their private database path.  The
        # fallback keeps the legacy standalone manager usable without importing
        # the public gateway during module initialization.
        if self._database_path_provider is not None:
            return self._database_path_provider()
        from .common import database_path

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
                    active = {
                        "fault_id": current.fault_id,
                        "started_at": current.started_at,
                        "expires_at": None,
                        "remaining_seconds": None,
                        "status": status,
                    }

        return {
            "cards": [dict(card) for card in FAULT_CARDS],
            "active": active,
            "lease_seconds": None,
            "delay_seconds": int(CHECKOUT_DELAY_SECONDS),
        }


demo_fault_manager = DemoFaultManager()
