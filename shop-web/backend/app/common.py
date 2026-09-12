"""Shared schemas, validation, SQLite helpers, and legacy migration utilities."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Iterator

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_PRICE = 1_000_000_000
MAX_PRODUCT_ID = 2**63 - 1
MAX_QUANTITY = 99
DEFAULT_DB_PATH = "/data/shop.db"
DEFAULT_LEGACY_DB_PATH = "/legacy/shop.db"
PRODUCT_FIELDS = ("name", "category", "price", "icon", "color", "description")
PRODUCT_TEXT_FIELDS = ("name", "category", "icon", "color", "description")
PRODUCT_SELECT = ", ".join(("id", *PRODUCT_FIELDS))
HEX_COLOR = r"^#[0-9a-fA-F]{6}$"

# These rows are inserted only when a legacy database has no products table
# or products rows.  A successful migration records its own version so a
# restart never re-imports or re-seeds data.
SEED_PRODUCTS = (
    dict(name="晨光陶瓷杯", category="居家生活", price=480, icon="☕", color="#e8d9c5", description="溫潤霧面釉色，盛裝每個美好的早晨。"),
    dict(name="日常帆布托特包", category="隨身好物", price=690, icon="👜", color="#dbe3d4", description="厚磅純棉、大容量，帶著喜歡的生活出門。"),
    dict(name="木質香氛蠟燭", category="居家生活", price=880, icon="🕯️", color="#ead9d3", description="雪松與佛手柑，為夜晚留一點安靜。"),
    dict(name="靈感方格筆記本", category="文具選物", price=320, icon="📓", color="#d5dedf", description="平攤裝訂與細緻紙張，收集生活的小靈感。"),
    dict(name="輕旅保溫水瓶", category="隨身好物", price=780, icon="🥤", color="#e5dfcd", description="輕巧不鏽鋼瓶身，剛剛好的隨行陪伴。"),
    dict(name="桌上綠意盆栽", category="居家生活", price=560, icon="🪴", color="#d9e3d7", description="一抹自然綠意，讓工作桌也能深呼吸。"),
)


def database_path(default: str = DEFAULT_DB_PATH, env_name: str = "DB_PATH") -> Path:
    configured = os.getenv(env_name, default).strip()
    if not configured:
        raise RuntimeError(f"{env_name} must point to a SQLite database file")
    return Path(configured)


@contextmanager
def connect(
    path: str | Path | None = None,
    *,
    timeout: float = 10,
    factory: type[sqlite3.Connection] = sqlite3.Connection,
) -> Iterator[sqlite3.Connection]:
    db_path = Path(path) if path is not None else database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path, timeout=timeout, factory=factory)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        with db:
            yield db
    finally:
        db.close()


@contextmanager
def connect_legacy_readonly() -> Iterator[sqlite3.Connection | None]:
    """Open LEGACY_DB_PATH in SQLite read-only mode when it is mounted."""
    configured = os.getenv("LEGACY_DB_PATH", DEFAULT_LEGACY_DB_PATH).strip()
    if not configured:
        raise RuntimeError("LEGACY_DB_PATH must point to a SQLite database file")
    legacy_path = Path(configured)
    if not legacy_path.exists():
        yield None
        return

    db = sqlite3.connect(
        f"file:{legacy_path}?mode=ro",
        uri=True,
        timeout=2,
    )
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only = ON")
        yield db
    finally:
        db.close()


def legacy_table_exists(db: sqlite3.Connection, table_name: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def request_fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


StrictProductId = Annotated[int, Field(strict=True, gt=0, le=MAX_PRODUCT_ID)]
StrictQuantity = Annotated[int, Field(strict=True, ge=1, le=MAX_QUANTITY)]
StrictPrice = Annotated[int, Field(strict=True, gt=0, le=MAX_PRICE)]


class ProductFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=40)
    price: StrictPrice
    icon: str = Field(min_length=1, max_length=32)
    color: str = Field(pattern=HEX_COLOR)
    description: str = Field(max_length=2000)

    @field_validator(*PRODUCT_TEXT_FIELDS, mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class ProductCreate(ProductFields):
    """The complete six-field payload used by POST and PUT."""


class ProductPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    category: str | None = Field(default=None, min_length=1, max_length=40)
    price: StrictPrice | None = None
    icon: str | None = Field(default=None, min_length=1, max_length=32)
    color: str | None = Field(default=None, pattern=HEX_COLOR)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="before")
    @classmethod
    def reject_null_and_empty_payload(cls, value):
        if isinstance(value, dict):
            if not value:
                raise ValueError("至少需要一個商品欄位")
            if any(raw_value is None for raw_value in value.values()):
                raise ValueError("商品欄位不可為 null")
        return value

    @field_validator(*PRODUCT_TEXT_FIELDS, mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_field(self):
        if not self.model_fields_set:
            raise ValueError("至少需要一個商品欄位")
        return self


class ProductResponse(ProductFields):
    id: StrictProductId


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: StrictProductId
    quantity: StrictQuantity


class Checkout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    address: str = Field(min_length=5, max_length=300)
    items: list[Item] = Field(min_length=1, max_length=50)

    @field_validator("name", "address", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class ShippingDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    address: str = Field(min_length=5, max_length=300)

    @field_validator("name", "address", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class CartItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: StrictProductId
    quantity: StrictQuantity


class CartQuantityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantity: StrictQuantity


class CartItemResponse(ProductResponse):
    product_id: StrictProductId
    quantity: StrictQuantity
    line_total: int = Field(ge=0)


class CartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    items: list[CartItemResponse]
    total: int = Field(ge=0)


class OrderLineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: StrictProductId
    name: str
    price: StrictPrice
    quantity: StrictQuantity


class OrderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    created_at: str
    total: int = Field(ge=0)
    items: list[OrderLineResponse]


class DemoFaultActivation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fault_id: str = Field(min_length=1, max_length=64)

    @field_validator("fault_id", mode="before")
    @classmethod
    def strip_fault_id(cls, value):
        return value.strip() if isinstance(value, str) else value


class DemoFaultCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fault_id: str
    title: str
    description: str


class DemoFaultActive(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fault_id: str
    started_at: str
    expires_at: str | None = None
    remaining_seconds: float | None = None
    status: str


class DemoFaultsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cards: list[DemoFaultCard]
    active: DemoFaultActive | None = None
    lease_seconds: int | None = None
    delay_seconds: int


class CartPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1, max_length=128)


class CartOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1, max_length=128)


class PreparedCartItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: StrictProductId
    quantity: StrictQuantity


class CartPrepareResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cart_id: str
    operation_id: str
    items: list[PreparedCartItem]


class CatalogLookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_ids: list[StrictProductId] = Field(min_length=1, max_length=50)


class CatalogLookupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    products: list[ProductResponse]
    missing_ids: list[StrictProductId]
