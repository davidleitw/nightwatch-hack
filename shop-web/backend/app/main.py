"""Public HTTP gateway for the catalog, cart, and order services."""

from __future__ import annotations

from contextlib import asynccontextmanager
from time import perf_counter
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Path as ApiPath, Request, Response
from monitor import MonitorConfig, get_invocation_id, install_logging, monitor

from .common import (
    CartItemInput,
    CartItemResponse,
    CartQuantityInput,
    CartResponse,
    Checkout,
    DemoFaultActivation,
    DemoFaultsResponse,
    MAX_PRODUCT_ID,
    OrderResponse,
    ProductCreate,
    ProductPatch,
    ProductResponse,
    ShippingDetails,
)
from .http_client import (
    DownstreamResponse,
    DownstreamUnavailableError,
    request_json,
    shutdown_http_clients,
)
from .logging_config import configure_logging, logger, shutdown_logging
from .monitoring import (
    CHECKOUT_REQUEST_MONITOR, PARENT_HEADER, exception_is_system_error,
    http_result_error, is_checkout_request,
)


CATALOG_URL_ENV = "CATALOG_URL"
CART_URL_ENV = "CART_URL"
ORDER_URL_ENV = "ORDER_URL"


def _service_url(env_name: str, default: str) -> str:
    # Environment values are read for each app import, so a container can use
    # the same image for gateway, catalog, cart, and order roles.
    import os

    return os.getenv(env_name, default).strip().rstrip("/")


CATALOG_URL = _service_url(CATALOG_URL_ENV, "http://catalog:8000")
CART_URL = _service_url(CART_URL_ENV, "http://cart:8000")
ORDER_URL = _service_url(ORDER_URL_ENV, "http://order:8000")


async def _proxy(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    headers: dict[str, str] | None = None,
    timeout_kind: str = "business",
    dependency: str,
) -> Response:
    if dependency == "order" and is_checkout_request(method, path):
        parent_id = get_invocation_id()
        if parent_id:
            headers = {**(headers or {}), PARENT_HEADER: parent_id}
    try:
        downstream = await request_json(
            base_url,
            path,
            method=method,
            payload=payload,
            headers=headers,
            timeout_kind=timeout_kind,
        )
    except DownstreamUnavailableError as exc:
        logger.error(
            "gateway downstream unavailable dependency=%s method=%s path=%s reason=%s",
            dependency,
            method,
            path,
            str(exc),
        )
        raise HTTPException(
            status_code=503,
            detail=f"{dependency} service unavailable",
        ) from exc

    response_headers: dict[str, str] = {}
    content_type = downstream.headers.get("content-type")
    if content_type:
        response_headers["content-type"] = content_type
    return Response(
        content=downstream.body,
        status_code=downstream.status,
        headers=response_headers,
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    install_logging(logger)
    try:
        logger.info(
            "gateway startup catalog_url=%s cart_url=%s order_url=%s",
            CATALOG_URL,
            CART_URL,
            ORDER_URL,
        )
        yield
    finally:
        logger.info("gateway shutdown")
        try:
            shutdown_http_clients()
        finally:
            shutdown_logging()


app = FastAPI(title="日日選物 API", lifespan=lifespan)


def _safe_path(request: Request) -> str:
    return request.url.path.replace("\r", r"\r").replace("\n", r"\n")


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    if is_checkout_request(request.method, request.url.path):
        return await _monitored_checkout_request(request, call_next)
    return await _logged_request(request, call_next)


@monitor(CHECKOUT_REQUEST_MONITOR, exception_is_error=exception_is_system_error,
         result_error=http_result_error)
async def _monitored_checkout_request(request: Request, call_next):
    return await _logged_request(request, call_next)


async def _logged_request(request: Request, call_next):
    started_at = perf_counter()
    path = _safe_path(request)
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception:
        logger.exception(
            "unexpected request error method=%s path=%s",
            request.method,
            path,
        )
        raise
    finally:
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            path,
            status_code,
            (perf_counter() - started_at) * 1000,
        )


@app.get("/api/health")
@monitor(MonitorConfig(name="GET /api/health", monitor_id="shop.health", level="WARNING"), exception_is_error=exception_is_system_error, result_error=http_result_error)
async def health():
    return await _proxy(
        ORDER_URL,
        "/internal/health",
        timeout_kind="control",
        dependency="order",
    )


@app.get("/api/products", response_model=list[ProductResponse])
@monitor(MonitorConfig(name="GET /api/products", monitor_id="shop.products", level="WARNING"), exception_is_error=exception_is_system_error, result_error=http_result_error)
async def products():
    return await _proxy(
        CATALOG_URL,
        "/api/products",
        dependency="catalog",
    )


@app.get("/api/products/{product_id}", response_model=ProductResponse)
async def get_product(product_id: int = ApiPath(..., gt=0, le=MAX_PRODUCT_ID)):
    return await _proxy(
        CATALOG_URL,
        f"/api/products/{product_id}",
        dependency="catalog",
    )


@app.post("/api/products", response_model=ProductResponse, status_code=201)
async def create_product(product: ProductCreate):
    return await _proxy(
        CATALOG_URL,
        "/api/products",
        method="POST",
        payload=product.model_dump(),
        dependency="catalog",
    )


@app.put("/api/products/{product_id}", response_model=ProductResponse)
async def replace_product(
    product: ProductCreate,
    product_id: int = ApiPath(..., gt=0, le=MAX_PRODUCT_ID),
):
    return await _proxy(
        CATALOG_URL,
        f"/api/products/{product_id}",
        method="PUT",
        payload=product.model_dump(),
        dependency="catalog",
    )


@app.patch("/api/products/{product_id}", response_model=ProductResponse)
async def patch_product(
    product: ProductPatch,
    product_id: int = ApiPath(..., gt=0, le=MAX_PRODUCT_ID),
):
    return await _proxy(
        CATALOG_URL,
        f"/api/products/{product_id}",
        method="PATCH",
        payload=product.model_dump(exclude_unset=True),
        dependency="catalog",
    )


@app.delete("/api/products/{product_id}", status_code=204)
async def delete_product(product_id: int = ApiPath(..., gt=0, le=MAX_PRODUCT_ID)):
    return await _proxy(
        CATALOG_URL,
        f"/api/products/{product_id}",
        method="DELETE",
        dependency="catalog",
    )


@app.post("/api/carts", response_model=CartResponse, status_code=201)
async def create_cart():
    return await _proxy(
        CART_URL,
        "/api/carts",
        method="POST",
        dependency="cart",
    )


@app.get("/api/carts/{cart_id}", response_model=CartResponse)
async def get_cart(cart_id: str = ApiPath(..., min_length=1)):
    return await _proxy(
        CART_URL,
        f"/api/carts/{quote(cart_id, safe='')}",
        dependency="cart",
    )


@app.delete("/api/carts/{cart_id}", status_code=204)
async def delete_cart(cart_id: str = ApiPath(..., min_length=1)):
    return await _proxy(
        CART_URL,
        f"/api/carts/{quote(cart_id, safe='')}",
        method="DELETE",
        timeout_kind="control",
        dependency="cart",
    )


@app.post("/api/carts/{cart_id}/items", response_model=CartResponse)
async def add_cart_item(
    item: CartItemInput,
    cart_id: str = ApiPath(..., min_length=1),
):
    return await _proxy(
        CART_URL,
        f"/api/carts/{quote(cart_id, safe='')}/items",
        method="POST",
        payload=item.model_dump(),
        dependency="cart",
    )


@app.get("/api/carts/{cart_id}/items", response_model=list[CartItemResponse])
async def get_cart_items(cart_id: str = ApiPath(..., min_length=1)):
    return await _proxy(
        CART_URL,
        f"/api/carts/{quote(cart_id, safe='')}/items",
        dependency="cart",
    )


@app.patch(
    "/api/carts/{cart_id}/items/{product_id}",
    response_model=CartResponse,
)
async def set_cart_item_quantity(
    quantity: CartQuantityInput,
    cart_id: str = ApiPath(..., min_length=1),
    product_id: int = ApiPath(..., gt=0, le=MAX_PRODUCT_ID),
):
    return await _proxy(
        CART_URL,
        f"/api/carts/{quote(cart_id, safe='')}/items/{product_id}",
        method="PATCH",
        payload=quantity.model_dump(),
        dependency="cart",
    )


@app.delete(
    "/api/carts/{cart_id}/items/{product_id}",
    response_model=CartResponse,
)
async def delete_cart_item(
    cart_id: str = ApiPath(..., min_length=1),
    product_id: int = ApiPath(..., gt=0, le=MAX_PRODUCT_ID),
):
    return await _proxy(
        CART_URL,
        f"/api/carts/{quote(cart_id, safe='')}/items/{product_id}",
        method="DELETE",
        dependency="cart",
    )


@app.delete("/api/carts/{cart_id}/items", response_model=CartResponse)
async def clear_cart(cart_id: str = ApiPath(..., min_length=1)):
    return await _proxy(
        CART_URL,
        f"/api/carts/{quote(cart_id, safe='')}/items",
        method="DELETE",
        dependency="cart",
    )


def _checkout_headers(idempotency_key: str | None) -> dict[str, str] | None:
    if idempotency_key is None:
        return None
    return {"Idempotency-Key": idempotency_key}


@app.post(
    "/api/carts/{cart_id}/checkout",
    response_model=OrderResponse,
    status_code=201,
)
async def checkout_cart(
    shipping: ShippingDetails,
    cart_id: str = ApiPath(..., min_length=1),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await _proxy(
        ORDER_URL,
        f"/api/carts/{quote(cart_id, safe='')}/checkout",
        method="POST",
        payload=shipping.model_dump(),
        headers=_checkout_headers(idempotency_key),
        dependency="order",
    )


@app.post("/api/orders", response_model=OrderResponse, status_code=201)
async def create_order(
    checkout: Checkout,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await _proxy(
        ORDER_URL,
        "/api/orders",
        method="POST",
        payload=checkout.model_dump(),
        headers=_checkout_headers(idempotency_key),
        dependency="order",
    )


@app.get("/api/demo-faults", response_model=DemoFaultsResponse)
async def get_demo_faults():
    return await _proxy(
        ORDER_URL,
        "/api/demo-faults",
        timeout_kind="control",
        dependency="order",
    )


@app.post("/api/demo-faults", response_model=DemoFaultsResponse)
async def activate_demo_fault(fault: DemoFaultActivation):
    return await _proxy(
        ORDER_URL,
        "/api/demo-faults",
        method="POST",
        payload=fault.model_dump(),
        timeout_kind="control",
        dependency="order",
    )


@app.delete("/api/demo-faults", response_model=DemoFaultsResponse)
async def deactivate_demo_fault():
    return await _proxy(
        ORDER_URL,
        "/api/demo-faults",
        method="DELETE",
        timeout_kind="control",
        dependency="order",
    )
