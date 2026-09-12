"""Standalone product catalog service for shop-web.

The catalog owns only product rows.  Cart and order data deliberately do not
live in this database: callers use the lookup endpoint to obtain a current
product representation when they need one.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Path as ApiPath, Response
from .logging_config import configure_logging, logger, shutdown_logging
from .monitoring import linked_service_request, observe
from .common import (
    CatalogLookupRequest,
    CatalogLookupResponse,
    MAX_PRODUCT_ID,
    PRODUCT_FIELDS,
    PRODUCT_SELECT,
    ProductCreate,
    ProductFields,
    ProductPatch,
    ProductResponse,
    SEED_PRODUCTS,
    StrictProductId,
    connect as shared_connect,
    connect_legacy_readonly,
    database_path as shared_database_path,
    legacy_table_exists,
)


DEFAULT_DB_PATH = "/data/shop.db"
DEFAULT_LEGACY_DB_PATH = "/legacy/shop.db"
SCHEMA_VERSION = 1

# Keep the service-local names convenient while sharing the exact wire models
# with the gateway and order service.
ProductLookupRequest = CatalogLookupRequest
ProductLookupResponse = CatalogLookupResponse


# These are the original shop-web seed rows.  They are inserted only when a
# fresh database has no legacy products table to import, or when the legacy
# database contains orders but no products table.
CREATE_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY
)
"""
CREATE_PRODUCTS_TABLE = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price INTEGER NOT NULL,
    icon TEXT NOT NULL,
    color TEXT NOT NULL,
    description TEXT NOT NULL
)
"""


def database_path() -> Path:
    return shared_database_path(DEFAULT_DB_PATH, "DB_PATH")


def legacy_database_path() -> Path:
    configured = os.getenv("LEGACY_DB_PATH", DEFAULT_LEGACY_DB_PATH).strip()
    return Path(configured) if configured else Path(DEFAULT_LEGACY_DB_PATH)


@contextmanager
def connect():
    with shared_connect(database_path(), timeout=10) as db:
        yield db


def _read_legacy_catalog() -> tuple[bool, bool, list[dict]] | None:
    # The shared helper opens the mounted legacy database in query-only mode;
    # no migration path writes to that file.
    with connect_legacy_readonly() as source:
        if source is None:
            return None
        has_products = legacy_table_exists(source, "products")
        has_orders = legacy_table_exists(source, "orders")
        rows: list[dict] = []
        if has_products:
            for row in source.execute(
                f"SELECT {PRODUCT_SELECT} FROM products ORDER BY id"
            ):
                rows.append({field: row[field] for field in ("id", *PRODUCT_FIELDS)})
        return has_products, has_orders, rows


def _schema_migrated(db: sqlite3.Connection) -> bool:
    return db.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?",
        (SCHEMA_VERSION,),
    ).fetchone() is not None


def _seed(db: sqlite3.Connection) -> None:
    db.executemany(
        """
        INSERT INTO products (name, category, price, icon, color, description)
        VALUES (:name, :category, :price, :icon, :color, :description)
        """,
        SEED_PRODUCTS,
    )


def init_db() -> None:
    """Create the catalog schema and perform a one-time legacy import."""
    legacy = _read_legacy_catalog()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(CREATE_MIGRATIONS_TABLE)
        db.execute(CREATE_PRODUCTS_TABLE)
        if not _schema_migrated(db):
            existing = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            if existing == 0:
                if legacy is None:
                    # No legacy database means this is a genuinely new shop.
                    _seed(db)
                elif legacy[0]:
                    # A products table is authoritative even when it is empty;
                    # do not silently recreate rows that an operator removed.
                    for product in legacy[2]:
                        db.execute(
                            """
                            INSERT INTO products
                                (id, name, category, price, icon, color, description)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            tuple(product[field] for field in ("id", *PRODUCT_FIELDS)),
                        )
                elif legacy[1]:
                    # The old migration created products when only an orders
                    # table existed.  Preserve that compatibility behavior.
                    _seed(db)
                # A legacy file with neither owner table is authoritative empty
                # state; leave the catalog empty.
            db.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )


def _product_payload(row: sqlite3.Row) -> dict:
    return {field: row[field] for field in ("id", *PRODUCT_FIELDS)}


def _find_product(db: sqlite3.Connection, product_id: int) -> sqlite3.Row | None:
    return db.execute(
        f"SELECT {PRODUCT_SELECT} FROM products WHERE id = ?",
        (product_id,),
    ).fetchone()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    try:
        init_db()
        logger.info("catalog service started")
        yield
    finally:
        logger.info("catalog service stopped")
        shutdown_logging()


app = FastAPI(title="日日選物 Catalog API", lifespan=lifespan)
app.middleware("http")(linked_service_request)


@app.get("/api/health")
def health():
    with connect() as db:
        db.execute("SELECT 1 FROM products LIMIT 1")
    return {"status": "ok"}


@app.get("/api/products", response_model=list[ProductResponse])
@observe("shop.catalog.read")
def products():
    with connect() as db:
        rows = db.execute(
            f"SELECT {PRODUCT_SELECT} FROM products ORDER BY id"
        ).fetchall()
    return [_product_payload(row) for row in rows]


@app.get("/api/products/{product_id}", response_model=ProductResponse)
@observe("shop.catalog.read")
def get_product(product_id: int = ApiPath(..., gt=0, le=MAX_PRODUCT_ID)):
    with connect() as db:
        row = _find_product(db, product_id)
    if row is None:
        raise HTTPException(status_code=404, detail="商品不存在。")
    return _product_payload(row)


@app.post("/api/products", response_model=ProductResponse, status_code=201)
def create_product(product: ProductCreate):
    values = product.model_dump()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            """
            INSERT INTO products (name, category, price, icon, color, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            tuple(values[field] for field in PRODUCT_FIELDS),
        )
        row = _find_product(db, cursor.lastrowid)
    return _product_payload(row)


@app.put("/api/products/{product_id}", response_model=ProductResponse)
def replace_product(
    product: ProductCreate,
    product_id: int = ApiPath(..., gt=0, le=MAX_PRODUCT_ID),
):
    values = product.model_dump()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if _find_product(db, product_id) is None:
            raise HTTPException(status_code=404, detail="商品不存在。")
        db.execute(
            """
            UPDATE products
               SET name = ?, category = ?, price = ?, icon = ?, description = ?, color = ?
             WHERE id = ?
            """,
            (
                values["name"],
                values["category"],
                values["price"],
                values["icon"],
                values["description"],
                values["color"],
                product_id,
            ),
        )
        row = _find_product(db, product_id)
    return _product_payload(row)


@app.patch("/api/products/{product_id}", response_model=ProductResponse)
def patch_product(
    product: ProductPatch,
    product_id: int = ApiPath(..., gt=0, le=MAX_PRODUCT_ID),
):
    values = product.model_dump(exclude_unset=True)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if _find_product(db, product_id) is None:
            raise HTTPException(status_code=404, detail="商品不存在。")
        assignments = ", ".join(f"{field} = ?" for field in values)
        db.execute(
            f"UPDATE products SET {assignments} WHERE id = ?",
            tuple(values.values()) + (product_id,),
        )
        row = _find_product(db, product_id)
    return _product_payload(row)


@app.delete("/api/products/{product_id}", status_code=204)
def delete_product(product_id: int = ApiPath(..., gt=0, le=MAX_PRODUCT_ID)):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        deleted = db.execute(
            "DELETE FROM products WHERE id = ?", (product_id,)
        ).rowcount
        if deleted == 0:
            raise HTTPException(status_code=404, detail="商品不存在。")
    return Response(status_code=204)


@app.post(
    "/internal/products/lookup",
    response_model=ProductLookupResponse,
)
@observe("shop.catalog.lookup")
def lookup_products(request: ProductLookupRequest):
    # Preserve first-seen request order while avoiding duplicate product rows.
    requested = list(dict.fromkeys(request.product_ids))
    if not requested:
        return {"products": [], "missing_ids": []}
    placeholders = ", ".join("?" for _ in requested)
    with connect() as db:
        rows = db.execute(
            f"SELECT {PRODUCT_SELECT} FROM products WHERE id IN ({placeholders})",
            tuple(requested),
        ).fetchall()
    by_id = {row["id"]: row for row in rows}
    return {
        "products": [_product_payload(by_id[product_id]) for product_id in requested if product_id in by_id],
        "missing_ids": [product_id for product_id in requested if product_id not in by_id],
    }


__all__ = [
    "ProductCreate",
    "ProductFields",
    "ProductLookupRequest",
    "ProductLookupResponse",
    "ProductPatch",
    "ProductResponse",
    "app",
    "connect",
    "database_path",
    "health",
    "init_db",
    "legacy_database_path",
    "lookup_products",
]
