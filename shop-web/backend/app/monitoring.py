"""Checkout result classification and order SQLite instrumentation."""
import sqlite3
from functools import partial

from fastapi import HTTPException, Response
from monitor import MonitorConfig, get_invocation_id, monitor

from .common import connect as sqlite_connect

PARENT_HEADER = "X-Nightwatch-Parent-Invocation"
CHECKOUT_REQUEST_MONITOR = MonitorConfig(
    name="shop checkout request", monitor_id="shop.checkout.request", level="WARNING",
)
CHECKOUT_LOGIC_MONITOR = MonitorConfig(
    name="shop checkout logic", monitor_id="shop.checkout.logic", level="WARNING",
)
DB_WRITE_MONITOR = MonitorConfig(
    name="shop database write", monitor_id="shop.db.write", level="WARNING",
)


def is_checkout_request(method: str, path: str) -> bool:
    return method == "POST" and (
        path == "/api/orders"
        or (path.startswith("/api/carts/") and path.endswith("/checkout"))
    )


def exception_is_system_error(exc: BaseException) -> bool:
    return not (isinstance(exc, HTTPException) and 400 <= exc.status_code < 500)


def http_result_error(response: Response) -> str | None:
    return f"HTTP {response.status_code}" if response.status_code >= 500 else None


class CheckoutConnection(sqlite3.Connection):
    """Measure checkout SQL and transaction completion, excluding business errors."""

    def execute(self, sql, parameters=()):
        operation = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        if get_invocation_id() and operation in {"BEGIN", "INSERT", "UPDATE", "DELETE", "REPLACE"}:
            return self._execute_write(sql, parameters)
        return super().execute(sql, parameters)

    @monitor(DB_WRITE_MONITOR)
    def _execute_write(self, sql, parameters):
        # Never record SQL parameters: they can contain customer data.
        return super().execute(sql, parameters)

    def __exit__(self, exc_type, exc, tb):
        if self.in_transaction and get_invocation_id():
            return self._finish_transaction(exc_type, exc, tb)
        return super().__exit__(exc_type, exc, tb)

    @monitor(DB_WRITE_MONITOR)
    def _finish_transaction(self, exc_type, exc, tb):
        # Successful rollback returns False; the business exception propagates
        # outside this invocation. SQLite commit/rollback failures still raise.
        return super().__exit__(exc_type, exc, tb)


connect = partial(sqlite_connect, factory=CheckoutConnection)
