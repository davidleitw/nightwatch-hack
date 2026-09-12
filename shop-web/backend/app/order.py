"""Order service and cross-service checkout coordinator."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import copy_context
from dataclasses import dataclass
from functools import partial
from time import perf_counter
from typing import Any, Callable
from collections.abc import Mapping
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Path as ApiPath, Request, Response
from monitor import MonitorConfig, install_logging, invocation_context, monitor

from .common import (
    CartOperationRequest,
    CartPrepareRequest,
    CartPrepareResponse,
    CatalogLookupRequest,
    CatalogLookupResponse,
    Checkout,
    DemoFaultActivation,
    DemoFaultsResponse,
    OrderResponse,
    ShippingDetails,
    connect_legacy_readonly,
    legacy_table_exists,
    request_fingerprint,
    utc_now_iso,
)
from .demo_faults import (
    DemoFaultConflictError,
    DemoFaultUnavailableError,
    DemoFaultManager,
)
from .http_client import (
    DownstreamResponse,
    DownstreamUnavailableError,
    request_json,
    shutdown_http_clients,
)
from .logging_config import configure_logging, logger, shutdown_logging
from .monitoring import CHECKOUT_LOGIC_MONITOR, PARENT_HEADER, connect, exception_is_system_error


DEFAULT_ORDER_DB_PATH = "/data/order.db"
ORDER_DB_ENV = "ORDER_DB_PATH"
RECONCILE_INTERVAL_SECONDS = 5.0

CREATE_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY
)
"""
CREATE_ORDERS_TABLE = """
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    payload BLOB NOT NULL
)
"""
CREATE_OPERATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS checkout_operations (
    operation_id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    request_fingerprint TEXT NOT NULL,
    cart_id TEXT,
    cart_operation_id TEXT,
    order_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('preparing', 'committed', 'done', 'aborted')),
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    items_payload BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_error TEXT
)
"""
CREATE_ATTEMPTS_TABLE = """
CREATE TABLE IF NOT EXISTS checkout_attempts (
    attempt_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL REFERENCES checkout_operations(operation_id),
    cart_operation_id TEXT,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT
)
"""


class OperationConflictError(Exception):
    """Raised when an idempotency key is reused for another request."""


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    idempotency_key: str | None
    request_fingerprint: str
    cart_id: str | None
    cart_operation_id: str | None
    order_id: str | None
    status: str
    name: str
    address: str
    items_payload: list[dict[str, Any]]
    created_at: str
    updated_at: str
    last_error: str | None


@dataclass(frozen=True)
class AttemptRecord:
    attempt_id: str
    operation_id: str
    cart_operation_id: str | None = None
    previous_cart_operation_id: str | None = None


def order_database_path() -> str:
    """Return the order service's private database path."""
    configured = os.getenv(ORDER_DB_ENV, "").strip()
    if not configured:
        configured = os.getenv("DB_PATH", DEFAULT_ORDER_DB_PATH).strip()
    if not configured:
        raise RuntimeError(f"{ORDER_DB_ENV} must point to a SQLite database file")
    return configured


def _payload_bytes(payload: object) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, memoryview):
        return payload.tobytes()
    if isinstance(payload, bytearray):
        return bytes(payload)
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _decode_json(payload: object) -> dict[str, Any]:
    decoded = json.loads(_payload_bytes(payload).decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("stored order payload must be an object")
    return decoded


def _public_order(payload: object) -> dict[str, Any]:
    order = _decode_json(payload)
    return {
        "id": order["id"],
        "created_at": order["created_at"],
        "total": order["total"],
        "items": order["items"],
    }


def _row_to_operation(row: Mapping[str, Any]) -> OperationRecord:
    raw_items = _decode_json(row["items_payload"])
    items = raw_items.get("items", [])
    if not isinstance(items, list):
        items = []
    return OperationRecord(
        operation_id=row["operation_id"],
        idempotency_key=row["idempotency_key"],
        request_fingerprint=row["request_fingerprint"],
        cart_id=row["cart_id"],
        cart_operation_id=row["cart_operation_id"],
        order_id=row["order_id"],
        status=row["status"],
        name=row["name"],
        address=row["address"],
        items_payload=items,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_error=row["last_error"],
    )


class OrderStore:
    """Run every order SQLite operation outside the event loop."""

    def __init__(self, path_provider: Callable[[], str] = order_database_path) -> None:
        self._path_provider = path_provider
        self.path = ""
        self._executor = ThreadPoolExecutor(
            max_workers=8,
            thread_name_prefix="shop-order-db",
        )
        self._closed = False

    async def _run(self, function: Callable[..., Any], *args: Any) -> Any:
        if self._closed:
            raise RuntimeError("order store is closed")
        loop = asyncio.get_running_loop()
        context = copy_context()
        return await loop.run_in_executor(self._executor, partial(context.run, function, *args))

    async def start(self) -> None:
        self.path = str(self._path_provider())
        await self._run(self._initialize_sync, self.path)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            partial(self._executor.shutdown, wait=True, cancel_futures=True),
        )

    @staticmethod
    def _initialize_sync(path: str) -> None:
        with connect(path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(CREATE_MIGRATIONS_TABLE)
            db.execute(CREATE_ORDERS_TABLE)
            db.execute(CREATE_OPERATIONS_TABLE)
            db.execute(CREATE_ATTEMPTS_TABLE)
            operation_columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(checkout_operations)")
            }
            if "cart_operation_id" not in operation_columns:
                db.execute(
                    "ALTER TABLE checkout_operations ADD COLUMN cart_operation_id TEXT"
                )
            attempt_columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(checkout_attempts)")
            }
            if "cart_operation_id" not in attempt_columns:
                db.execute(
                    "ALTER TABLE checkout_attempts ADD COLUMN cart_operation_id TEXT"
                )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_checkout_operations_status "
                "ON checkout_operations(status, updated_at)"
            )
            migrated = db.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 1"
            ).fetchone()
            if migrated is None:
                OrderStore._import_legacy_orders(db)
                db.execute("INSERT INTO schema_migrations (version) VALUES (1)")

    @staticmethod
    def _import_legacy_orders(db: sqlite3.Connection) -> None:
        with connect_legacy_readonly() as legacy:
            if legacy is None or not legacy_table_exists(legacy, "orders"):
                return
            rows = legacy.execute(
                "SELECT id, created_at, payload FROM orders"
            ).fetchall()
            for row in rows:
                payload = _payload_bytes(row["payload"])
                db.execute(
                    """
                    INSERT OR IGNORE INTO orders (id, created_at, payload)
                    VALUES (?, ?, ?)
                    """,
                    (str(row["id"]), str(row["created_at"]), sqlite3.Binary(payload)),
                )

    @staticmethod
    @monitor(MonitorConfig(name="shop order database query", monitor_id="shop.db.query", level="WARNING"))
    def _health_sync(path: str) -> None:
        with connect(path) as db:
            db.execute("SELECT 1 FROM orders LIMIT 1").fetchone()

    async def health(self) -> None:
        await self._run(self._health_sync, self.path)

    @staticmethod
    def _begin_operation_sync(
        path: str,
        idempotency_key: str | None,
        fingerprint: str,
        cart_id: str | None,
        name: str,
        address: str,
        items: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        now = utc_now_iso()
        items_payload = json.dumps({"items": items}, ensure_ascii=False).encode("utf-8")
        with connect(path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = None
            if idempotency_key is not None:
                row = db.execute(
                    "SELECT * FROM checkout_operations WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
            if row is not None:
                if row["request_fingerprint"] != fingerprint:
                    raise OperationConflictError(
                        "Idempotency-Key 已用於不同的結帳內容。"
                    )
                if row["status"] == "aborted":
                    db.execute(
                        """
                        UPDATE checkout_operations
                           SET status = 'preparing', updated_at = ?, last_error = NULL,
                               cart_id = ?, cart_operation_id = NULL, name = ?,
                               address = ?, items_payload = ?
                         WHERE operation_id = ?
                        """,
                        (
                            now,
                            cart_id,
                            name,
                            address,
                            sqlite3.Binary(items_payload),
                            row["operation_id"],
                        ),
                    )
                    row = db.execute(
                        "SELECT * FROM checkout_operations WHERE operation_id = ?",
                        (row["operation_id"],),
                    ).fetchone()
                return dict(row), True

            operation_id = uuid4().hex
            db.execute(
                """
                INSERT INTO checkout_operations
                    (operation_id, idempotency_key, request_fingerprint, cart_id,
                     cart_operation_id, order_id, status, name, address, items_payload,
                     created_at, updated_at, last_error)
                VALUES (?, ?, ?, ?, NULL, NULL, 'preparing', ?, ?, ?, ?, ?, NULL)
                """,
                (
                    operation_id,
                    idempotency_key,
                    fingerprint,
                    cart_id,
                    name,
                    address,
                    sqlite3.Binary(items_payload),
                    now,
                    now,
                ),
            )
            row = db.execute(
                "SELECT * FROM checkout_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            return dict(row), False

    async def begin_operation(
        self,
        *,
        idempotency_key: str | None,
        fingerprint: str,
        cart_id: str | None,
        name: str,
        address: str,
        items: list[dict[str, Any]],
    ) -> tuple[OperationRecord, bool]:
        row, existing = await self._run(
            self._begin_operation_sync,
            self.path,
            idempotency_key,
            fingerprint,
            cart_id,
            name,
            address,
            items,
        )
        return _row_to_operation(row), existing

    @staticmethod
    def _update_items_sync(
        path: str,
        operation_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        encoded = json.dumps({"items": items}, ensure_ascii=False).encode("utf-8")
        with connect(path) as db:
            db.execute(
                """
                UPDATE checkout_operations
                   SET items_payload = ?, updated_at = ?
                 WHERE operation_id = ?
                """,
                (sqlite3.Binary(encoded), utc_now_iso(), operation_id),
            )

    async def update_items(self, operation_id: str, items: list[dict[str, Any]]) -> None:
        await self._run(self._update_items_sync, self.path, operation_id, items)

    @staticmethod
    def _start_attempt_sync(path: str, operation_id: str) -> AttemptRecord:
        attempt_id = uuid4().hex
        now = utc_now_iso()
        with connect(path) as db:
            db.execute("BEGIN IMMEDIATE")
            operation = db.execute(
                "SELECT cart_id, cart_operation_id FROM checkout_operations "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if operation is None:
                raise RuntimeError("checkout operation does not exist")
            previous_cart_operation_id = operation["cart_operation_id"]
            cart_operation_id = uuid4().hex if operation["cart_id"] is not None else None
            db.execute(
                """
                INSERT INTO checkout_attempts
                    (attempt_id, operation_id, cart_operation_id, phase, status,
                     started_at, updated_at, error)
                VALUES (?, ?, ?, 'prepare', 'active', ?, ?, NULL)
                """,
                (
                    attempt_id,
                    operation_id,
                    cart_operation_id,
                    now,
                    now,
                ),
            )
            db.execute(
                """
                UPDATE checkout_operations
                   SET status = 'preparing', cart_operation_id = ?,
                       updated_at = ?, last_error = NULL
                 WHERE operation_id = ?
                """,
                (cart_operation_id, now, operation_id),
            )
        return AttemptRecord(
            attempt_id,
            operation_id,
            cart_operation_id,
            previous_cart_operation_id,
        )

    async def start_attempt(self, operation_id: str) -> AttemptRecord:
        return await self._run(self._start_attempt_sync, self.path, operation_id)

    @staticmethod
    def _update_attempt_sync(
        path: str,
        attempt_id: str,
        phase: str,
        status: str,
        error: str | None,
    ) -> None:
        with connect(path) as db:
            db.execute(
                """
                UPDATE checkout_attempts
                   SET phase = ?, status = ?, updated_at = ?, error = ?
                 WHERE attempt_id = ?
                """,
                (phase, status, utc_now_iso(), error, attempt_id),
            )

    async def update_attempt(
        self,
        attempt_id: str,
        phase: str,
        status: str = "active",
        error: str | None = None,
    ) -> None:
        await self._run(
            self._update_attempt_sync,
            self.path,
            attempt_id,
            phase,
            status,
            error,
        )

    @staticmethod
    def _commit_order_sync(
        path: str,
        operation_id: str,
        attempt_id: str,
        order: dict[str, Any],
        stored_payload: bytes,
        should_raise: Callable[[], str | None],
    ) -> None:
        with connect(path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO orders (id, created_at, payload) VALUES (?, ?, ?)",
                (
                    order["id"],
                    order["created_at"],
                    sqlite3.Binary(stored_payload),
                ),
            )
            now = utc_now_iso()
            db.execute(
                """
                UPDATE checkout_operations
                   SET status = 'committed', order_id = ?, updated_at = ?, last_error = NULL
                 WHERE operation_id = ?
                """,
                (order["id"], now, operation_id),
            )
            db.execute(
                """
                UPDATE checkout_attempts
                   SET phase = 'commit', status = 'committed', updated_at = ?, error = NULL
                 WHERE attempt_id = ?
                """,
                (now, attempt_id),
            )
            fault_id = should_raise()
            if fault_id == "checkout_exception":
                # This exception is raised after both real writes and before
                # the connection context can commit either one.
                raise RuntimeError("demo fault checkout_exception")

    async def commit_order(
        self,
        operation_id: str,
        attempt_id: str,
        order: dict[str, Any],
        stored_payload: bytes,
        should_raise: Callable[[], str | None],
    ) -> None:
        await self._run(
            self._commit_order_sync,
            self.path,
            operation_id,
            attempt_id,
            order,
            stored_payload,
            should_raise,
        )

    @staticmethod
    def _mark_done_sync(path: str, operation_id: str, attempt_id: str | None) -> None:
        with connect(path) as db:
            db.execute("BEGIN IMMEDIATE")
            now = utc_now_iso()
            db.execute(
                """
                UPDATE checkout_operations
                   SET status = 'done', updated_at = ?, last_error = NULL
                 WHERE operation_id = ? AND status = 'committed'
                """,
                (now, operation_id),
            )
            if attempt_id is None:
                db.execute(
                    """
                    UPDATE checkout_attempts
                       SET phase = 'complete', status = 'done', updated_at = ?, error = NULL
                     WHERE operation_id = ? AND status IN ('active', 'committed')
                    """,
                    (now, operation_id),
                )
            else:
                db.execute(
                    """
                    UPDATE checkout_attempts
                       SET phase = 'complete', status = 'done', updated_at = ?, error = NULL
                     WHERE attempt_id = ?
                    """,
                    (now, attempt_id),
                )

    async def mark_done(self, operation_id: str, attempt_id: str | None = None) -> None:
        await self._run(self._mark_done_sync, self.path, operation_id, attempt_id)

    @staticmethod
    def _mark_aborted_sync(
        path: str,
        operation_id: str,
        attempt_id: str | None,
        error: str,
    ) -> None:
        with connect(path) as db:
            db.execute("BEGIN IMMEDIATE")
            now = utc_now_iso()
            db.execute(
                """
                UPDATE checkout_operations
                   SET status = 'aborted', updated_at = ?, last_error = ?
                 WHERE operation_id = ? AND status = 'preparing'
                """,
                (now, error[:1000], operation_id),
            )
            if attempt_id is None:
                db.execute(
                    """
                    UPDATE checkout_attempts
                       SET phase = 'abort', status = 'aborted', updated_at = ?, error = ?
                     WHERE operation_id = ? AND status = 'active'
                    """,
                    (now, error[:1000], operation_id),
                )
            else:
                db.execute(
                    """
                    UPDATE checkout_attempts
                       SET phase = 'abort', status = 'aborted', updated_at = ?, error = ?
                     WHERE attempt_id = ?
                    """,
                    (now, error[:1000], attempt_id),
                )

    async def mark_aborted(
        self,
        operation_id: str,
        error: str,
        attempt_id: str | None = None,
    ) -> None:
        await self._run(
            self._mark_aborted_sync,
            self.path,
            operation_id,
            attempt_id,
            error,
        )

    @staticmethod
    def _set_attempt_error_sync(path: str, attempt_id: str, error: str) -> None:
        with connect(path) as db:
            db.execute(
                "UPDATE checkout_attempts SET error = ?, updated_at = ? WHERE attempt_id = ?",
                (error[:1000], utc_now_iso(), attempt_id),
            )

    async def set_attempt_error(self, attempt_id: str, error: str) -> None:
        await self._run(self._set_attempt_error_sync, self.path, attempt_id, error)

    @staticmethod
    def _reconcile_sync(path: str) -> list[OperationRecord]:
        with connect(path) as db:
            rows = db.execute(
                """
                SELECT * FROM checkout_operations
                 WHERE status IN ('preparing', 'committed')
                 ORDER BY updated_at, operation_id
                """
            ).fetchall()
        return [_row_to_operation(row) for row in rows]

    async def reconcile_operations(self) -> list[OperationRecord]:
        return await self._run(self._reconcile_sync, self.path)

    @staticmethod
    def _cart_operation_ids_sync(path: str, operation_id: str) -> list[str]:
        with connect(path) as db:
            rows = db.execute(
                """
                SELECT cart_operation_id FROM checkout_attempts
                 WHERE operation_id = ? AND cart_operation_id IS NOT NULL
                UNION
                SELECT cart_operation_id FROM checkout_operations
                 WHERE operation_id = ? AND cart_operation_id IS NOT NULL
                ORDER BY cart_operation_id
                """,
                (operation_id, operation_id),
            ).fetchall()
        return [str(row[0]) for row in rows]

    async def cart_operation_ids(self, operation_id: str) -> list[str]:
        return await self._run(
            self._cart_operation_ids_sync,
            self.path,
            operation_id,
        )

    @staticmethod
    def _get_operation_sync(path: str, operation_id: str) -> OperationRecord | None:
        with connect(path) as db:
            row = db.execute(
                "SELECT * FROM checkout_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return _row_to_operation(row) if row is not None else None

    async def get_operation(self, operation_id: str) -> OperationRecord | None:
        return await self._run(self._get_operation_sync, self.path, operation_id)

    @staticmethod
    def _get_order_sync(path: str, order_id: str) -> dict[str, Any] | None:
        with connect(path) as db:
            row = db.execute(
                "SELECT payload FROM orders WHERE id = ?",
                (order_id,),
            ).fetchone()
        return _public_order(row["payload"]) if row is not None else None

    async def get_order(self, order_id: str) -> dict[str, Any] | None:
        return await self._run(self._get_order_sync, self.path, order_id)



class OrderService:
    """Own order persistence and coordinate cart/catalog checkout calls."""

    def __init__(self) -> None:
        self.store = OrderStore()
        self.catalog_url = os.getenv("CATALOG_URL", "http://catalog:8000").strip().rstrip("/")
        self.cart_url = os.getenv("CART_URL", "http://cart:8000").strip().rstrip("/")
        self.fault_manager = DemoFaultManager(lambda: self.store.path)
        self._active_operations: set[str] = set()
        self._active_lock = asyncio.Lock()
        self._coordination_lock = asyncio.Lock()
        self._stop_event: asyncio.Event | None = None
        self._reconcile_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        logger.info("order service startup")
        logger.info("order database migration started path=%s", self.store.path or "configured")
        await self.store.start()
        logger.info("order database migration completed path=%s", self.store.path)
        await self.fault_manager.start()
        await self.reconcile_once()
        self._stop_event = asyncio.Event()
        self._reconcile_task = asyncio.create_task(
            self._reconcile_loop(),
            name="shop-order-reconcile",
        )

    async def shutdown(self) -> None:
        logger.info("order service shutdown")
        stop_event = self._stop_event
        if stop_event is not None:
            stop_event.set()
        task = self._reconcile_task
        self._reconcile_task = None
        if task is not None and not task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass
        try:
            await self.fault_manager.shutdown()
        finally:
            try:
                await self.store.close()
            finally:
                shutdown_http_clients()
        self._stop_event = None
        logger.info("order service stopped")

    async def _reconcile_loop(self) -> None:
        stop_event = self._stop_event
        if stop_event is None:
            return
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=RECONCILE_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                pass
            if stop_event.is_set():
                return
            try:
                await self.reconcile_once()
            except Exception:
                logger.exception("checkout reconciliation failed")

    async def _claim_reconcile_operation(self, operation_id: str) -> bool:
        async with self._active_lock:
            if operation_id in self._active_operations:
                return False
            self._active_operations.add(operation_id)
            return True

    async def _release_operation(self, operation_id: str) -> None:
        async with self._active_lock:
            self._active_operations.discard(operation_id)

    async def reconcile_once(self) -> None:
        # Snapshot and claim under one short coordination section.  Network
        # calls happen after release so a control timeout cannot block checkout
        # creation or another reconciliation candidate.
        async with self._coordination_lock:
            operations = await self.store.reconcile_operations()
            claimed_operations = [
                operation
                for operation in operations
                if await self._claim_reconcile_operation(operation.operation_id)
            ]
        for operation in claimed_operations:
            try:
                if operation.status == "preparing":
                    await self._reconcile_preparing(operation)
                elif operation.status == "committed":
                    await self._reconcile_committed(operation)
            except Exception:
                logger.exception(
                    "checkout reconciliation operation_id=%s status=%s",
                    operation.operation_id,
                    operation.status,
                )
            finally:
                await self._release_operation(operation.operation_id)

    async def _reconcile_preparing(self, operation: OperationRecord) -> None:
        if operation.cart_id is not None:
            cart_operation_ids = await self.store.cart_operation_ids(
                operation.operation_id
            )
            if not cart_operation_ids:
                cart_operation_ids = [
                    operation.cart_operation_id or operation.operation_id
                ]
            try:
                for cart_operation_id in cart_operation_ids:
                    await self._cart_operation(
                        operation.cart_id,
                        cart_operation_id,
                        "abort",
                    )
            except Exception:
                logger.exception(
                    "checkout reconcile abort failed operation_id=%s cart_id=%s",
                    operation.operation_id,
                    operation.cart_id,
                )
                return
        await self.store.mark_aborted(
            operation.operation_id,
            "recovered incomplete checkout",
        )
        logger.info(
            "checkout operation aborted during reconcile operation_id=%s reason=startup_or_retry",
            operation.operation_id,
        )

    async def _reconcile_committed(self, operation: OperationRecord) -> None:
        if operation.cart_id is not None:
            await self._cart_operation(
                operation.cart_id,
                operation.cart_operation_id or operation.operation_id,
                "complete",
            )
        await self.store.mark_done(operation.operation_id)
        logger.info(
            "checkout operation completed during reconcile operation_id=%s order_id=%s",
            operation.operation_id,
            operation.order_id,
        )

    async def health(self) -> dict[str, str]:
        await self.store.health()
        return {"status": "ok"}

    async def _request_dependency(
        self,
        base_url: str,
        path: str,
        *,
        method: str = "GET",
        payload: object | None = None,
        headers: dict[str, str] | None = None,
        timeout_kind: str = "business",
        dependency: str,
    ) -> DownstreamResponse:
        try:
            response = await request_json(
                base_url,
                path,
                method=method,
                payload=payload,
                headers=headers,
                timeout_kind=timeout_kind,
            )
        except DownstreamUnavailableError as exc:
            logger.error(
                "downstream unavailable dependency=%s path=%s reason=%s",
                dependency,
                path,
                str(exc),
            )
            raise HTTPException(
                status_code=503,
                detail=f"{dependency} service unavailable",
            ) from exc
        if response.status < 200 or response.status >= 300:
            logger.error(
                "downstream error dependency=%s path=%s status=%s",
                dependency,
                path,
                response.status,
            )
            raise HTTPException(
                status_code=response.status,
                detail=_response_detail(response),
            )
        return response

    async def _prepare_cart(
        self,
        cart_id: str,
        cart_operation_id: str,
    ) -> list[dict[str, int]]:
        path = f"/internal/carts/{quote(cart_id, safe='')}/prepare"
        response = await self._request_dependency(
            self.cart_url,
            path,
            method="POST",
            payload=CartPrepareRequest(operation_id=cart_operation_id).model_dump(),
            dependency="cart",
        )
        try:
            prepared = CartPrepareResponse.model_validate_json(response.body)
        except (ValueError, TypeError) as exc:
            logger.error(
                "invalid cart prepare response cart_operation_id=%s cart_id=%s",
                cart_operation_id,
                cart_id,
                exc_info=True,
            )
            raise HTTPException(status_code=503, detail="cart service returned invalid data") from exc
        return [item.model_dump() for item in prepared.items]

    async def _cart_operation(
        self,
        cart_id: str,
        operation_id: str,
        operation: str,
    ) -> None:
        path = (
            f"/internal/carts/{quote(cart_id, safe='')}/{operation}"
        )
        await self._request_dependency(
            self.cart_url,
            path,
            method="POST",
            payload=CartOperationRequest(operation_id=operation_id).model_dump(),
            timeout_kind="control",
            dependency="cart",
        )

    async def _lookup_products(
        self,
        product_ids: list[int],
    ) -> CatalogLookupResponse:
        request = CatalogLookupRequest(product_ids=product_ids)
        response = await self._request_dependency(
            self.catalog_url,
            "/internal/products/lookup",
            method="POST",
            payload=request.model_dump(),
            dependency="catalog",
        )
        try:
            return CatalogLookupResponse.model_validate_json(response.body)
        except (ValueError, TypeError) as exc:
            logger.error(
                "invalid catalog lookup response product_count=%s",
                len(product_ids),
                exc_info=True,
            )
            raise HTTPException(status_code=503, detail="catalog service returned invalid data") from exc

    async def _abort_before_commit(
        self,
        operation: OperationRecord,
        attempt_id: str,
        reason: str,
        cart_operation_id: str | None,
        previous_cart_operation_id: str | None = None,
    ) -> None:
        if operation.cart_id is not None:
            cart_operation_ids: list[str] = []
            for candidate in (
                previous_cart_operation_id,
                cart_operation_id,
                operation.cart_operation_id,
                operation.operation_id,
            ):
                if candidate is not None and candidate not in cart_operation_ids:
                    cart_operation_ids.append(candidate)
            try:
                # prepare may have committed remotely before its response was
                # lost, so abort every durable pre-commit cart operation.
                for cart_operation_id in cart_operation_ids:
                    await self._cart_operation(
                        operation.cart_id,
                        cart_operation_id,
                        "abort",
                    )
            except Exception:
                logger.exception(
                    "checkout cart abort failed operation_id=%s cart_id=%s",
                    operation.operation_id,
                    operation.cart_id,
                )
                return
        try:
            await self.store.mark_aborted(operation.operation_id, reason, attempt_id)
        except Exception:
            logger.exception(
                "checkout operation abort record failed operation_id=%s",
                operation.operation_id,
            )

    async def _finish_committed(self, operation: OperationRecord) -> dict[str, Any]:
        if operation.order_id is None:
            raise HTTPException(status_code=500, detail="已提交訂單缺少訂單 ID。")
        order = await self.store.get_order(operation.order_id)
        if order is None:
            raise HTTPException(status_code=500, detail="已提交訂單資料不存在。")
        if operation.status == "committed":
            if operation.cart_id is not None:
                try:
                    await self._cart_operation(
                        operation.cart_id,
                        operation.cart_operation_id or operation.operation_id,
                        "complete",
                    )
                except HTTPException as exc:
                    logger.error(
                        "checkout retry cart complete failed operation_id=%s cart_id=%s",
                        operation.operation_id,
                        operation.cart_id,
                    )
                    raise HTTPException(
                        status_code=503,
                        detail="購物車服務無法完成已提交結帳。",
                    ) from exc
            await self.store.mark_done(operation.operation_id)
        return order

    async def checkout_order(
        self,
        checkout: Checkout,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        return await self._checkout(
            name=checkout.name,
            address=checkout.address,
            requested_items=[item.model_dump() for item in checkout.items],
            cart_id=None,
            idempotency_key=idempotency_key,
            fingerprint_payload={
                "kind": "order",
                "name": checkout.name,
                "address": checkout.address,
                "items": [item.model_dump() for item in checkout.items],
            },
        )

    async def checkout_cart(
        self,
        cart_id: str,
        shipping: ShippingDetails,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        return await self._checkout(
            name=shipping.name,
            address=shipping.address,
            requested_items=[],
            cart_id=cart_id,
            idempotency_key=idempotency_key,
            fingerprint_payload={
                "kind": "cart",
                "cart_id": cart_id,
                "name": shipping.name,
                "address": shipping.address,
            },
        )

    @monitor(CHECKOUT_LOGIC_MONITOR, exception_is_error=exception_is_system_error)
    async def _checkout(
        self,
        *,
        name: str,
        address: str,
        requested_items: list[dict[str, Any]],
        cart_id: str | None,
        idempotency_key: str | None,
        fingerprint_payload: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_key = _normalize_idempotency_key(idempotency_key)
        fingerprint = request_fingerprint(fingerprint_payload)
        committed_operation: OperationRecord | None = None
        async with self._coordination_lock:
            try:
                operation, existing = await self.store.begin_operation(
                    idempotency_key=normalized_key,
                    fingerprint=fingerprint,
                    cart_id=cart_id,
                    name=name,
                    address=address,
                    items=requested_items,
                )
            except OperationConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if existing and operation.status in {"done", "committed"}:
                committed_operation = operation
            elif not await self._claim_reconcile_operation(operation.operation_id):
                raise HTTPException(status_code=409, detail="此結帳操作正在處理中。")
        if committed_operation is not None:
            return await self._finish_committed(committed_operation)

        attempt: AttemptRecord | None = None
        committed = False
        try:
            attempt = await self.store.start_attempt(operation.operation_id)
            if cart_id is not None:
                await self.store.update_attempt(attempt.attempt_id, "prepare")
                cart_operation_id = attempt.cart_operation_id or operation.operation_id
                if (
                    attempt.previous_cart_operation_id is not None
                    and attempt.previous_cart_operation_id != cart_operation_id
                ):
                    # A retry after an aborted attempt may need a fresh cart
                    # operation ID. Release the old reservation before prepare.
                    await self._cart_operation(
                        cart_id,
                        attempt.previous_cart_operation_id,
                        "abort",
                    )
                prepared_items = await self._prepare_cart(cart_id, cart_operation_id)
                if not prepared_items:
                    raise HTTPException(status_code=400, detail="購物車是空的。")
                await self.store.update_items(operation.operation_id, prepared_items)
            else:
                prepared_items = requested_items

            normalized_items = _aggregate_items(prepared_items)
            await self.store.update_attempt(attempt.attempt_id, "catalog")
            lookup = await self._lookup_products(
                [item["product_id"] for item in normalized_items]
            )
            products = {product.id: product for product in lookup.products}
            if lookup.missing_ids:
                raise HTTPException(
                    status_code=400,
                    detail="商品不存在，請重新整理商品列表。",
                )
            lines = []
            for item in normalized_items:
                product = products.get(item["product_id"])
                if product is None:
                    raise HTTPException(
                        status_code=400,
                        detail="商品不存在，請重新整理商品列表。",
                    )
                lines.append(
                    {
                        "product_id": product.id,
                        "name": product.name,
                        "price": product.price,
                        "quantity": item["quantity"],
                    }
                )

            order = {
                "id": uuid4().hex,
                "created_at": utc_now_iso(),
                "total": sum(line["price"] * line["quantity"] for line in lines),
                "items": lines,
            }
            stored_payload = json.dumps(
                {**order, "name": name, "address": address},
                ensure_ascii=False,
            ).encode("utf-8")
            await self.store.update_attempt(attempt.attempt_id, "commit")
            await self.store.commit_order(
                operation.operation_id,
                attempt.attempt_id,
                order,
                stored_payload,
                self.fault_manager.checkout_fault_id,
            )
            committed = True
            if cart_id is not None:
                await self.store.update_attempt(attempt.attempt_id, "complete")
                try:
                    await self._cart_operation(
                        cart_id,
                        attempt.cart_operation_id or operation.operation_id,
                        "complete",
                    )
                except HTTPException as exc:
                    logger.error(
                        "checkout cart complete failed after commit operation_id=%s cart_id=%s",
                        operation.operation_id,
                        cart_id,
                    )
                    raise HTTPException(
                        status_code=503,
                        detail="購物車服務無法完成已提交結帳。",
                    ) from exc
            await self.store.mark_done(operation.operation_id, attempt.attempt_id)
            logger.info(
                "checkout committed operation_id=%s order_id=%s cart_id=%s",
                operation.operation_id,
                order["id"],
                cart_id,
            )
            return order
        except Exception as exc:
            if not committed and attempt is not None:
                await self._abort_before_commit(
                    operation,
                    attempt.attempt_id,
                    str(exc) or exc.__class__.__name__,
                    attempt.cart_operation_id,
                    attempt.previous_cart_operation_id,
                )
            raise
        finally:
            await self._release_operation(operation.operation_id)


# The conversion is deliberately kept outside request handlers so downstream
# data is validated before it can become a stored order snapshot.
def _aggregate_items(items: list[dict[str, Any]]) -> list[dict[str, int]]:
    quantities: dict[int, int] = {}
    for item in items:
        product_id = int(item["product_id"])
        quantity = int(item["quantity"])
        merged = quantities.get(product_id, 0) + quantity
        if merged > 99:
            raise HTTPException(status_code=400, detail="每件商品最多可購買 99 件。")
        quantities[product_id] = merged
    return [
        {"product_id": product_id, "quantity": quantity}
        for product_id, quantity in quantities.items()
    ]


def _normalize_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > 256:
        raise HTTPException(status_code=422, detail="Idempotency-Key 過長。")
    return value


def _response_detail(response: DownstreamResponse) -> str:
    try:
        value = json.loads(response.body.decode("utf-8"))
        if isinstance(value, dict) and isinstance(value.get("detail"), str):
            return value["detail"]
        if isinstance(value, str):
            return value
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    text = response.body.decode("utf-8", errors="replace").strip()
    return text[:500] or f"downstream returned HTTP {response.status}"


order_service = OrderService()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    install_logging(logger)
    try:
        await order_service.start()
        yield
    finally:
        try:
            await order_service.shutdown()
        finally:
            shutdown_logging()


order_app = FastAPI(title="日日選物 Order API", lifespan=lifespan)
app = order_app


def _safe_path(request: Request) -> str:
    return request.url.path.replace("\r", r"\r").replace("\n", r"\n")


def _is_checkout_request(method: str, path: str) -> bool:
    return method == "POST" and (
        path == "/api/orders"
        or (path.startswith("/api/carts/") and path.endswith("/checkout"))
    )


@order_app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    with invocation_context(request.headers.get(PARENT_HEADER)):
        return await _logged_request(request, call_next)


async def _logged_request(request: Request, call_next):
    started_at = perf_counter()
    path = _safe_path(request)
    status_code = 500
    affected_fault_id = None
    try:
        if _is_checkout_request(request.method, path):
            affected_fault_id = order_service.fault_manager.checkout_fault_id()
            if affected_fault_id == "checkout_delay":
                await order_service.fault_manager.wait_for_checkout_delay()
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception:
        logger.exception(
            "unexpected request error method=%s path=%s fault_id=%s",
            request.method,
            path,
            affected_fault_id,
        )
        raise
    finally:
        duration_ms = (perf_counter() - started_at) * 1000
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            path,
            status_code,
            duration_ms,
        )
        if affected_fault_id is not None:
            logger.info(
                "demo fault affected request fault_id=%s method=%s path=%s status=%s duration_ms=%.2f",
                affected_fault_id,
                request.method,
                path,
                status_code,
                duration_ms,
            )


@order_app.get("/api/health")
@monitor(MonitorConfig(name="GET /api/health", monitor_id="shop.order.health", level="WARNING"))
async def health():
    return await order_service.health()


@order_app.get("/internal/health")
async def internal_health():
    return await order_service.health()


@order_app.get("/api/demo-faults", response_model=DemoFaultsResponse)
async def get_demo_faults():
    return await order_service.fault_manager.get_state()


@order_app.post("/api/demo-faults", response_model=DemoFaultsResponse)
async def activate_demo_fault(fault: DemoFaultActivation):
    try:
        return await order_service.fault_manager.activate(fault.fault_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DemoFaultConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DemoFaultUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@order_app.delete("/api/demo-faults", response_model=DemoFaultsResponse)
async def deactivate_demo_fault():
    return await order_service.fault_manager.deactivate()


@order_app.post("/api/orders", response_model=OrderResponse, status_code=201)
async def create_order(
    checkout: Checkout,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await order_service.checkout_order(checkout, idempotency_key)


@order_app.post(
    "/api/carts/{cart_id}/checkout",
    response_model=OrderResponse,
    status_code=201,
)
async def checkout_cart(
    shipping: ShippingDetails,
    cart_id: str = ApiPath(..., min_length=1),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await order_service.checkout_cart(cart_id, shipping, idempotency_key)
