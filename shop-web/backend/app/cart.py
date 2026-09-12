"""Standalone anonymous cart service for shop-web.

The cart database owns carts, quantities, and durable checkout operations.  It
does not create a SQLite foreign key to catalog data: product details are
looked up over the internal catalog protocol whenever a public cart response
or checkout preparation needs them.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Path as ApiPath, Response
from .logging_config import configure_logging, logger, shutdown_logging
from .monitoring import linked_service_request, observe

from .common import (
    CartItemInput,
    CartItemResponse,
    CartOperationRequest,
    CartPrepareRequest,
    CartPrepareResponse,
    CartQuantityInput,
    CartResponse,
    CatalogLookupResponse,
    MAX_PRODUCT_ID,
    ProductResponse,
    PreparedCartItem,
    StrictProductId,
    StrictQuantity,
    connect as shared_connect,
    connect_legacy_readonly,
    database_path as shared_database_path,
    legacy_table_exists,
    utc_now_iso,
)

from .http_client import (
    DownstreamUnavailableError,
    request_json,
    shutdown_http_clients,
)


DEFAULT_DB_PATH = "/data/shop.db"
DEFAULT_LEGACY_DB_PATH = "/legacy/shop.db"
DEFAULT_CATALOG_URL = "http://catalog:8000"
MAX_QUANTITY = 99
MAX_OPERATION_ID = 128
SCHEMA_VERSION = 1
PREPARED = "prepared"
COMPLETED = "completed"
ABORTED = "aborted"


CREATE_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY
)
"""
CREATE_CARTS_TABLE = """
CREATE TABLE IF NOT EXISTS carts (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
)
"""
# There is intentionally no product_id foreign key: catalog owns another
# SQLite database.  Missing product IDs are removed after a successful lookup.
CREATE_CART_ITEMS_TABLE = """
CREATE TABLE IF NOT EXISTS cart_items (
    cart_id TEXT NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 99),
    PRIMARY KEY (cart_id, product_id)
)
"""
CREATE_CHECKOUT_OPERATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS checkout_operations (
    operation_id TEXT PRIMARY KEY,
    cart_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('prepared', 'completed', 'aborted')),
    snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT
)
"""
CREATE_ACTIVE_OPERATION_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS checkout_one_active_per_cart
    ON checkout_operations(cart_id)
    WHERE state = 'prepared'
"""
# Cache is only a display fallback for a cart already reserved for checkout.
# It is not authoritative and is refreshed from catalog before normal output.
CREATE_PRODUCT_CACHE_TABLE = """
CREATE TABLE IF NOT EXISTS product_cache (
    product_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price INTEGER NOT NULL,
    icon TEXT NOT NULL,
    color TEXT NOT NULL,
    description TEXT NOT NULL
)
"""


# Service-local aliases retain the names used while the gateway contract was
# being wired, but all Pydantic classes now come from app.common.
CheckoutOperationRequest = CartOperationRequest
CheckoutItem = PreparedCartItem
CheckoutPrepareResponse = CartPrepareResponse
ProductLookupResponse = CatalogLookupResponse


class ProductLookupError(RuntimeError):
    """The catalog could not be reached or returned an invalid response."""


class ProductLookupUnavailable(ProductLookupError):
    """Catalog network/HTTP failure; this must never become a missing ID."""


def database_path() -> Path:
    return shared_database_path(DEFAULT_DB_PATH, "DB_PATH")


def legacy_database_path() -> Path:
    return shared_database_path(DEFAULT_LEGACY_DB_PATH, "LEGACY_DB_PATH")


def catalog_base_url() -> str:
    configured = (
        os.getenv("CATALOG_URL")
        or os.getenv("CATALOG_BASE_URL")
        or DEFAULT_CATALOG_URL
    ).strip()
    if not configured:
        raise RuntimeError("CATALOG_URL must point to the catalog service")
    return configured.rstrip("/")


@contextmanager
def connect():
    with shared_connect(database_path(), timeout=10) as db:
        yield db


def _read_legacy_cart() -> tuple[bool, bool, list[dict], list[dict]] | None:
    with connect_legacy_readonly() as source:
        if source is None:
            return None
        has_carts = legacy_table_exists(source, "carts")
        has_items = legacy_table_exists(source, "cart_items")
        carts: list[dict] = []
        items: list[dict] = []
        if has_carts:
            carts = [
                {"id": row["id"], "created_at": row["created_at"]}
                for row in source.execute(
                    "SELECT id, created_at FROM carts ORDER BY created_at, id"
                )
            ]
        if has_items:
            items = [
                {
                    "cart_id": row["cart_id"],
                    "product_id": row["product_id"],
                    "quantity": row["quantity"],
                }
                for row in source.execute(
                    """
                    SELECT cart_id, product_id, quantity
                      FROM cart_items
                     ORDER BY cart_id, product_id
                    """
                )
            ]
        return has_carts, has_items, carts, items


def _schema_migrated(db: sqlite3.Connection) -> bool:
    return db.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?",
        (SCHEMA_VERSION,),
    ).fetchone() is not None


def init_db() -> None:
    """Create cart-owned tables and import the legacy owner tables once."""
    legacy = _read_legacy_cart()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(CREATE_MIGRATIONS_TABLE)
        db.execute(CREATE_CARTS_TABLE)
        db.execute(CREATE_CART_ITEMS_TABLE)
        db.execute(CREATE_CHECKOUT_OPERATIONS_TABLE)
        db.execute(CREATE_ACTIVE_OPERATION_INDEX)
        db.execute(CREATE_PRODUCT_CACHE_TABLE)
        if not _schema_migrated(db):
            existing = db.execute("SELECT COUNT(*) FROM carts").fetchone()[0]
            if existing == 0 and legacy is not None and legacy[0]:
                for cart in legacy[2]:
                    db.execute(
                        "INSERT INTO carts (id, created_at) VALUES (?, ?)",
                        (cart["id"], cart["created_at"]),
                    )
                for item in legacy[3]:
                    db.execute(
                        """
                        INSERT INTO cart_items (cart_id, product_id, quantity)
                        VALUES (?, ?, ?)
                        """,
                        (item["cart_id"], item["product_id"], item["quantity"]),
                    )
            db.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )


def _now() -> str:
    return utc_now_iso()


def _require_cart(db: sqlite3.Connection, cart_id: str) -> None:
    if db.execute("SELECT 1 FROM carts WHERE id = ?", (cart_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="購物車不存在。")


def _active_operation(db: sqlite3.Connection, cart_id: str) -> sqlite3.Row | None:
    return db.execute(
        """
        SELECT operation_id, cart_id, state, snapshot, created_at, finished_at
          FROM checkout_operations
         WHERE cart_id = ? AND state = 'prepared'
        """,
        (cart_id,),
    ).fetchone()


def _reject_reserved(db: sqlite3.Connection, cart_id: str) -> None:
    if _active_operation(db, cart_id) is not None:
        raise HTTPException(status_code=409, detail="購物車正在結帳保留中。")


def _raw_items(db: sqlite3.Connection, cart_id: str) -> list[dict]:
    return [
        {
            "product_id": row["product_id"],
            "quantity": row["quantity"],
        }
        for row in db.execute(
            """
            SELECT product_id, quantity
              FROM cart_items
             WHERE cart_id = ?
             ORDER BY product_id
            """,
            (cart_id,),
        )
    ]


def _decode_snapshot(raw: str) -> list[dict]:
    try:
        decoded = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("checkout operation snapshot is invalid") from exc
    if not isinstance(decoded, list):
        raise RuntimeError("checkout operation snapshot is invalid")
    return [CheckoutItem.model_validate(item).model_dump() for item in decoded]


@observe("shop.cart.catalog.lookup")
async def _lookup_catalog(product_ids: list[int]) -> tuple[dict[int, dict], set[int]]:
    unique_ids = list(dict.fromkeys(product_ids))
    if not unique_ids:
        return {}, set()
    products_by_id: dict[int, dict] = {}
    missing_ids: set[int] = set()
    # app.common validates the internal body to at most 50 IDs.  A cart can
    # contain more lines, so use several exact protocol calls rather than
    # weakening that shared validation.
    for offset in range(0, len(unique_ids), 50):
        batch = unique_ids[offset : offset + 50]
        try:
            response = await request_json(
                catalog_base_url(),
                "/internal/products/lookup",
                method="POST",
                payload={"product_ids": batch},
                timeout_kind="business",
            )
        except DownstreamUnavailableError as exc:
            raise ProductLookupUnavailable("catalog lookup is unavailable") from exc
        if response.status != 200:
            raise ProductLookupUnavailable(
                f"catalog lookup returned HTTP {response.status}"
            )
        try:
            decoded = json.loads(response.body.decode("utf-8"))
            result = ProductLookupResponse.model_validate(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ProductLookupUnavailable("catalog lookup response is invalid") from exc
        batch_products = {product.id: product.model_dump() for product in result.products}
        batch_missing = set(result.missing_ids)
        # Treat omissions as missing only when the catalog explicitly reports
        # them; a malformed overlap is a protocol error rather than deletion.
        if set(batch_products) & batch_missing or set(batch_products) | batch_missing != set(batch):
            raise ProductLookupUnavailable("catalog lookup response IDs are invalid")
        products_by_id.update(batch_products)
        missing_ids.update(batch_missing)
    return products_by_id, missing_ids


def _cache_products(products: dict[int, dict]) -> None:
    if not products:
        return
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.executemany(
            """
            INSERT INTO product_cache
                (product_id, name, category, price, icon, color, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                price = excluded.price,
                icon = excluded.icon,
                color = excluded.color,
                description = excluded.description
            """,
            [
                (
                    product_id,
                    product["name"],
                    product["category"],
                    product["price"],
                    product["icon"],
                    product["color"],
                    product["description"],
                )
                for product_id, product in products.items()
            ],
        )


def _cached_products(product_ids: set[int]) -> dict[int, dict]:
    if not product_ids:
        return {}
    placeholders = ", ".join("?" for _ in product_ids)
    with connect() as db:
        rows = db.execute(
            f"""
            SELECT product_id, name, category, price, icon, color, description
              FROM product_cache
             WHERE product_id IN ({placeholders})
            """,
            tuple(product_ids),
        ).fetchall()
    return {
        row["product_id"]: {
            "id": row["product_id"],
            "name": row["name"],
            "category": row["category"],
            "price": row["price"],
            "icon": row["icon"],
            "color": row["color"],
            "description": row["description"],
        }
        for row in rows
    }


def _prune_missing(cart_id: str, missing: set[int]) -> None:
    if not missing:
        return
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _require_cart(db, cart_id)
        # A reservation may have appeared while catalog lookup was in flight.
        # Do not mutate a reserved cart; its durable prepare snapshot owns it.
        if _active_operation(db, cart_id) is not None:
            return
        placeholders = ", ".join("?" for _ in missing)
        db.execute(
            f"DELETE FROM cart_items WHERE cart_id = ? AND product_id IN ({placeholders})",
            (cart_id, *missing),
        )


async def _cart_payload(cart_id: str) -> dict:
    with connect() as db:
        _require_cart(db, cart_id)
        raw_items = _raw_items(db, cart_id)
        reserved = _active_operation(db, cart_id) is not None
    if not raw_items:
        return {"id": cart_id, "items": [], "total": 0}

    product_ids = [item["product_id"] for item in raw_items]
    try:
        products, missing = await _lookup_catalog(product_ids)
    except ProductLookupError as exc:
        raise HTTPException(status_code=503, detail="商品服務暫時無法使用。") from exc
    _cache_products(products)
    if missing and not reserved:
        _prune_missing(cart_id, missing)
        with connect() as db:
            _require_cart(db, cart_id)
            raw_items = _raw_items(db, cart_id)
            reserved = _active_operation(db, cart_id) is not None
        if not raw_items:
            return {"id": cart_id, "items": [], "total": 0}
        product_ids = [item["product_id"] for item in raw_items]
        # The successful response above is still authoritative for products it
        # returned; a concurrent product removal can only make an ID missing.
        products = {product_id: products[product_id] for product_id in product_ids if product_id in products}
        missing = set(product_ids) - set(products)
    if missing and reserved:
        # A prepared cart must remain readable.  The cache was populated during
        # add/prepare in normal operation; use it if the catalog has since
        # removed a product.  If there is no cache, surface the inconsistency
        # instead of inventing product data or silently deleting the snapshot.
        products.update(_cached_products(missing))
        missing = set(product_ids) - set(products)
        if missing:
            raise HTTPException(status_code=503, detail="保留中的商品資料無法取得。")

    output: list[dict] = []
    for item in raw_items:
        product = products.get(item["product_id"])
        if product is None:
            continue
        line = dict(product)
        line.update(
            product_id=item["product_id"],
            quantity=item["quantity"],
            line_total=product["price"] * item["quantity"],
        )
        output.append(line)
    return {
        "id": cart_id,
        "items": output,
        "total": sum(item["line_total"] for item in output),
    }


async def _public_catalog_product(product_id: int) -> None:
    try:
        products, missing = await _lookup_catalog([product_id])
    except ProductLookupError as exc:
        raise HTTPException(status_code=503, detail="商品服務暫時無法使用。") from exc
    if missing or product_id not in products:
        raise HTTPException(status_code=404, detail="商品不存在。")
    _cache_products(products)


def _operation_response(row: sqlite3.Row) -> dict:
    return {
        "cart_id": row["cart_id"],
        "operation_id": row["operation_id"],
        "items": _decode_snapshot(row["snapshot"]),
    }


def _same_items(left: list[dict], right: list[dict]) -> bool:
    return [
        (item["product_id"], item["quantity"])
        for item in left
    ] == [
        (item["product_id"], item["quantity"])
        for item in right
    ]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    try:
        init_db()
        logger.info("cart service started")
        yield
    finally:
        logger.info("cart service stopped")
        try:
            shutdown_http_clients()
        finally:
            shutdown_logging()


app = FastAPI(title="日日選物 Cart API", lifespan=lifespan)
app.middleware("http")(linked_service_request)


@app.get("/api/health")
def health():
    with connect() as db:
        db.execute("SELECT 1 FROM carts LIMIT 1")
    return {"status": "ok"}


@app.post("/api/carts", response_model=CartResponse, status_code=201)
@observe("shop.cart.mutate")
async def create_cart():
    cart_id = uuid4().hex
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT INTO carts (id, created_at) VALUES (?, ?)",
            (cart_id, _now()),
        )
    return await _cart_payload(cart_id)


@app.get("/api/carts/{cart_id}", response_model=CartResponse)
@observe("shop.cart.read")
async def get_cart(cart_id: str = ApiPath(..., min_length=1)):
    return await _cart_payload(cart_id)


@app.delete("/api/carts/{cart_id}", status_code=204)
@observe("shop.cart.mutate")
def delete_cart(cart_id: str = ApiPath(..., min_length=1)):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _require_cart(db, cart_id)
        _reject_reserved(db, cart_id)
        db.execute("DELETE FROM cart_items WHERE cart_id = ?", (cart_id,))
        db.execute("DELETE FROM carts WHERE id = ?", (cart_id,))
    return Response(status_code=204)


@app.post("/api/carts/{cart_id}/items", response_model=CartResponse)
@observe("shop.cart.mutate")
async def add_cart_item(
    item: CartItemInput,
    cart_id: str = ApiPath(..., min_length=1),
):
    # Check local ownership first so a reserved or missing cart gets its
    # contract status without depending on catalog availability.  The catalog
    # RPC remains outside every SQLite transaction, and the same checks are
    # repeated below after the RPC to close the reservation race.
    with connect() as db:
        db.execute("BEGIN")
        _require_cart(db, cart_id)
        _reject_reserved(db, cart_id)
    await _public_catalog_product(item.product_id)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _require_cart(db, cart_id)
        _reject_reserved(db, cart_id)
        current = db.execute(
            "SELECT quantity FROM cart_items WHERE cart_id = ? AND product_id = ?",
            (cart_id, item.product_id),
        ).fetchone()
        if current is None:
            db.execute(
                "INSERT INTO cart_items (cart_id, product_id, quantity) VALUES (?, ?, ?)",
                (cart_id, item.product_id, item.quantity),
            )
        else:
            quantity = current["quantity"] + item.quantity
            if quantity > MAX_QUANTITY:
                raise HTTPException(status_code=400, detail="每件商品最多可購買 99 件。")
            db.execute(
                "UPDATE cart_items SET quantity = ? WHERE cart_id = ? AND product_id = ?",
                (quantity, cart_id, item.product_id),
            )
    return await _cart_payload(cart_id)


@app.get("/api/carts/{cart_id}/items", response_model=list[CartItemResponse])
@observe("shop.cart.read")
async def get_cart_items(cart_id: str = ApiPath(..., min_length=1)):
    return (await _cart_payload(cart_id))["items"]


@app.patch(
    "/api/carts/{cart_id}/items/{product_id}",
    response_model=CartResponse,
)
@observe("shop.cart.mutate")
async def set_cart_item_quantity(
    quantity: CartQuantityInput,
    cart_id: str = ApiPath(..., min_length=1),
    product_id: int = ApiPath(..., gt=0, le=MAX_PRODUCT_ID),
):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _require_cart(db, cart_id)
        _reject_reserved(db, cart_id)
        updated = db.execute(
            """
            UPDATE cart_items
               SET quantity = ?
             WHERE cart_id = ? AND product_id = ?
            """,
            (quantity.quantity, cart_id, product_id),
        ).rowcount
        if updated == 0:
            raise HTTPException(status_code=404, detail="購物車商品不存在。")
    return await _cart_payload(cart_id)


@app.delete(
    "/api/carts/{cart_id}/items/{product_id}",
    response_model=CartResponse,
)
@observe("shop.cart.mutate")
async def delete_cart_item(
    cart_id: str = ApiPath(..., min_length=1),
    product_id: int = ApiPath(..., gt=0, le=MAX_PRODUCT_ID),
):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _require_cart(db, cart_id)
        _reject_reserved(db, cart_id)
        deleted = db.execute(
            "DELETE FROM cart_items WHERE cart_id = ? AND product_id = ?",
            (cart_id, product_id),
        ).rowcount
        if deleted == 0:
            raise HTTPException(status_code=404, detail="購物車商品不存在。")
    return await _cart_payload(cart_id)


@app.delete("/api/carts/{cart_id}/items", response_model=CartResponse)
@observe("shop.cart.mutate")
async def clear_cart(cart_id: str = ApiPath(..., min_length=1)):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _require_cart(db, cart_id)
        _reject_reserved(db, cart_id)
        db.execute("DELETE FROM cart_items WHERE cart_id = ?", (cart_id,))
    return await _cart_payload(cart_id)


@app.post(
    "/internal/carts/{cart_id}/prepare",
    response_model=CheckoutPrepareResponse,
)
@observe("shop.cart.prepare")
async def prepare_checkout(
    request: CartPrepareRequest,
    cart_id: str = ApiPath(..., min_length=1),
):
    # Existing operation IDs are durable and replayable.  Check these first so
    # retrying after a completed/aborted operation never touches new cart rows.
    with connect() as db:
        db.execute("BEGIN")
        existing = db.execute(
            "SELECT operation_id, cart_id, state, snapshot, created_at, finished_at "
            "FROM checkout_operations WHERE operation_id = ?",
            (request.operation_id,),
        ).fetchone()
        if existing is not None:
            if existing["cart_id"] != cart_id:
                raise HTTPException(status_code=409, detail="checkout operation 已被其他購物車使用。")
            return _operation_response(existing)
        _require_cart(db, cart_id)
        active = _active_operation(db, cart_id)
        if active is not None:
            raise HTTPException(status_code=400, detail="購物車已在其他 checkout operation 中。")
        first_items = _raw_items(db, cart_id)
    if not first_items:
        raise HTTPException(status_code=400, detail="購物車是空的。")

    try:
        products, missing = await _lookup_catalog([item["product_id"] for item in first_items])
    except ProductLookupError as exc:
        raise HTTPException(status_code=503, detail="商品服務暫時無法使用。") from exc
    _cache_products(products)
    if missing:
        _prune_missing(cart_id, missing)
        raise HTTPException(status_code=404, detail="商品不存在。")

    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT operation_id, cart_id, state, snapshot, created_at, finished_at "
            "FROM checkout_operations WHERE operation_id = ?",
            (request.operation_id,),
        ).fetchone()
        if existing is not None:
            if existing["cart_id"] != cart_id:
                raise HTTPException(status_code=409, detail="checkout operation 已被其他購物車使用。")
            return _operation_response(existing)
        _require_cart(db, cart_id)
        if _active_operation(db, cart_id) is not None:
            raise HTTPException(status_code=400, detail="購物車已在其他 checkout operation 中。")
        current_items = _raw_items(db, cart_id)
        if not current_items:
            raise HTTPException(status_code=400, detail="購物車是空的。")
        # If a local mutation won the small lookup race, do not reserve an
        # unvalidated row.  The caller can retry with a new operation ID.
        if not _same_items(first_items, current_items):
            raise HTTPException(status_code=409, detail="購物車在 checkout prepare 期間已變更。")
        snapshot = json.dumps(current_items, ensure_ascii=False, separators=(",", ":"))
        try:
            db.execute(
                """
                INSERT INTO checkout_operations
                    (operation_id, cart_id, state, snapshot, created_at)
                VALUES (?, ?, 'prepared', ?, ?)
                """,
                (request.operation_id, cart_id, snapshot, _now()),
            )
        except sqlite3.IntegrityError as exc:
            # A concurrent operation can win after the first read.  Return the
            # public concurrency result instead of leaking SQLite details.
            raise HTTPException(status_code=400, detail="購物車已在其他 checkout operation 中。") from exc
        row = db.execute(
            "SELECT operation_id, cart_id, state, snapshot, created_at, finished_at "
            "FROM checkout_operations WHERE operation_id = ?",
            (request.operation_id,),
        ).fetchone()
    return _operation_response(row)


def _finish_checkout(
    request: CheckoutOperationRequest,
    cart_id: str,
    final_state: str,
) -> Response:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT operation_id, cart_id, state, snapshot, created_at, finished_at "
            "FROM checkout_operations WHERE operation_id = ?",
            (request.operation_id,),
        ).fetchone()
        if row is None:
            if final_state == ABORTED:
                # Persist cancellation even when prepare has not arrived yet.
                # Its replay must not reserve the cart after order has aborted.
                db.execute(
                    "INSERT INTO checkout_operations "
                    "(operation_id, cart_id, state, snapshot, created_at, finished_at) "
                    "VALUES (?, ?, 'aborted', '[]', ?, ?)",
                    (request.operation_id, cart_id, _now(), _now()),
                )
                return Response(status_code=204)
            raise HTTPException(status_code=404, detail="checkout operation 不存在。")
        if row["cart_id"] != cart_id:
            raise HTTPException(status_code=409, detail="checkout operation 已被其他購物車使用。")
        if row["state"] != PREPARED:
            # completed/aborted records are intentionally retained.  A retry
            # must not clear any cart items added after that operation ended.
            return Response(status_code=204)
        if final_state == COMPLETED:
            snapshot = _decode_snapshot(row["snapshot"])
            for item in snapshot:
                db.execute(
                    "DELETE FROM cart_items WHERE cart_id = ? AND product_id = ?",
                    (cart_id, item["product_id"]),
                )
        db.execute(
            """
            UPDATE checkout_operations
               SET state = ?, finished_at = ?
             WHERE operation_id = ? AND state = 'prepared'
            """,
            (final_state, _now(), request.operation_id),
        )
    return Response(status_code=204)


@app.post("/internal/carts/{cart_id}/complete", status_code=204)
@observe("shop.cart.complete")
def complete_checkout(
    request: CheckoutOperationRequest,
    cart_id: str = ApiPath(..., min_length=1),
):
    return _finish_checkout(request, cart_id, COMPLETED)


@app.post("/internal/carts/{cart_id}/abort", status_code=204)
@observe("shop.cart.abort")
def abort_checkout(
    request: CheckoutOperationRequest,
    cart_id: str = ApiPath(..., min_length=1),
):
    return _finish_checkout(request, cart_id, ABORTED)


cart_app = app


__all__ = [
    "CartItemInput",
    "CartItemResponse",
    "CartQuantityInput",
    "CartResponse",
    "CheckoutItem",
    "CheckoutOperationRequest",
    "CheckoutPrepareResponse",
    "ProductLookupError",
    "ProductLookupUnavailable",
    "abort_checkout",
    "app",
    "cart_app",
    "catalog_base_url",
    "complete_checkout",
    "connect",
    "create_cart",
    "database_path",
    "init_db",
    "legacy_database_path",
    "prepare_checkout",
]
