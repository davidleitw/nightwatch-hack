"""Generate low-volume, realistic shop traffic for local observability."""

from __future__ import annotations

import json
import logging
import os
import random
import signal
import time
import urllib.error
import urllib.request


LOGGER = logging.getLogger("shop.traffic")
STOP = False


def env_float(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except ValueError:
        return default


BASE_URL = os.getenv("TRAFFIC_BASE_URL", "http://backend:8000").rstrip("/")
INTERVAL_SECONDS = env_float("TRAFFIC_INTERVAL_SECONDS", 10.0, 1.0)
CHECKOUT_PROBABILITY = min(
    1.0, env_float("TRAFFIC_CHECKOUT_PROBABILITY", 0.2, 0.0)
)
REQUEST_TIMEOUT_SECONDS = env_float("TRAFFIC_REQUEST_TIMEOUT_SECONDS", 12.0, 1.0)


def stop(_signum: int, _frame: object) -> None:
    global STOP
    STOP = True


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


def request(method: str, path: str, body: dict | None = None) -> tuple[int, object | None]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json", "User-Agent": "nightwatch-traffic/1.0"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{BASE_URL}{path}", data=data, headers=headers, method=method
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = response.read()
        return response.status, json.loads(payload) if payload else None


def run_cycle() -> None:
    status, products = request("GET", "/api/products")
    if status != 200 or not isinstance(products, list) or not products:
        raise RuntimeError(f"product lookup returned status={status}")

    product = random.choice(products)
    product_id = product["id"]
    status, cart = request("POST", "/api/carts")
    if status != 201 or not isinstance(cart, dict) or not cart.get("id"):
        raise RuntimeError(f"cart creation returned status={status}")
    cart_id = cart["id"]

    try:
        request("GET", f"/api/carts/{cart_id}")
        request("POST", f"/api/carts/{cart_id}/items", {"product_id": product_id, "quantity": 1})
        request("PATCH", f"/api/carts/{cart_id}/items/{product_id}", {"quantity": 2})
        request("GET", f"/api/carts/{cart_id}/items")
        if random.random() < CHECKOUT_PROBABILITY:
            request(
                "POST",
                f"/api/carts/{cart_id}/checkout",
                {"name": "觀測流量", "address": "NightWatch 測試環境"},
            )
        else:
            request("DELETE", f"/api/carts/{cart_id}/items")
    finally:
        # A successful checkout already removes the cart; DELETE is idempotent
        # from the generator's perspective and prevents abandoned carts.
        try:
            request("DELETE", f"/api/carts/{cart_id}")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
            pass


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    LOGGER.info(
        "traffic generator started base_url=%s interval_seconds=%s checkout_probability=%s",
        BASE_URL,
        INTERVAL_SECONDS,
        CHECKOUT_PROBABILITY,
    )
    while not STOP:
        started = time.monotonic()
        try:
            run_cycle()
            LOGGER.info("traffic cycle completed")
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
            KeyError,
            RuntimeError,
        ) as exc:
            LOGGER.warning("traffic cycle failed: %s", exc)
        remaining = max(0.0, INTERVAL_SECONDS - (time.monotonic() - started))
        if remaining:
            time.sleep(remaining)
    LOGGER.info("traffic generator stopped")


if __name__ == "__main__":
    main()
