"""Small standard-library HTTP client with separate business/control pools."""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
import threading
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BUSINESS_TIMEOUT_SECONDS = 15.0
CONTROL_TIMEOUT_SECONDS = 2.0

_executor_lock = threading.Lock()
_business_executor: ThreadPoolExecutor | None = None
_control_executor: ThreadPoolExecutor | None = None


class DownstreamUnavailableError(RuntimeError):
    """Raised when a service cannot be reached before its timeout."""


@dataclass(frozen=True)
class DownstreamResponse:
    status: int
    headers: dict[str, str]
    body: bytes


def _executor_for(timeout_kind: str) -> tuple[ThreadPoolExecutor, float]:
    global _business_executor, _control_executor
    with _executor_lock:
        if timeout_kind == "business":
            if _business_executor is None:
                _business_executor = ThreadPoolExecutor(
                    max_workers=24,
                    thread_name_prefix="shop-gateway-business-http",
                )
            return _business_executor, BUSINESS_TIMEOUT_SECONDS
        if timeout_kind == "control":
            if _control_executor is None:
                _control_executor = ThreadPoolExecutor(
                    max_workers=4,
                    thread_name_prefix="shop-gateway-control-http",
                )
            return _control_executor, CONTROL_TIMEOUT_SECONDS
    raise ValueError("timeout_kind must be 'business' or 'control'")


def _request_sync(
    url: str,
    method: str,
    body: bytes | None,
    headers: Mapping[str, str],
    timeout: float,
) -> DownstreamResponse:
    request = Request(
        url,
        data=body,
        headers=dict(headers),
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return DownstreamResponse(
                status=response.status,
                headers={key.lower(): value for key, value in response.headers.items()},
                body=response.read(),
            )
    except HTTPError as exc:
        # HTTPError is also a response; preserving its body/status lets the
        # gateway keep downstream 4xx/5xx response shapes unchanged.
        try:
            response_body = exc.read()
        finally:
            exc.close()
        return DownstreamResponse(
            status=exc.code,
            headers={key.lower(): value for key, value in exc.headers.items()},
            body=response_body,
        )
    except (OSError, URLError, TimeoutError) as exc:
        raise DownstreamUnavailableError(str(exc) or "downstream request failed") from exc


async def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    headers: Mapping[str, str] | None = None,
    timeout_kind: str = "business",
) -> DownstreamResponse:
    """Issue a JSON request without blocking the event loop."""
    executor, timeout = _executor_for(timeout_kind)

    request_headers = {
        "Accept": "application/json",
        **(headers or {}),
    }
    encoded_body = None
    if payload is not None:
        encoded_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    loop = asyncio.get_running_loop()
    call = partial(
        _request_sync,
        url,
        method.upper(),
        encoded_body,
        request_headers,
        timeout,
    )
    return await loop.run_in_executor(executor, call)


def shutdown_http_clients() -> None:
    """Stop executors created by this module during application shutdown."""
    global _business_executor, _control_executor
    with _executor_lock:
        business_executor = _business_executor
        control_executor = _control_executor
        _business_executor = None
        _control_executor = None
    if business_executor is not None:
        business_executor.shutdown(wait=False, cancel_futures=True)
    if control_executor is not None:
        control_executor.shutdown(wait=False, cancel_futures=True)
