"""Opt-in actuator for the shop's published /api/demo-faults contract."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from jsonschema import Draft202012Validator

from .graph import NoRedirect
from .loop import Observation, encode

EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}
REPAIR_TOOLS = [
    {"name": "get_demo_faults", "parameters": EMPTY,
     "description": "Read the configured shop's GET /api/demo-faults. Returns cards, active fault (or null), lease_seconds and delay_seconds. This is explicit demo-control state, not independent diagnostic evidence. Read before attempting deactivation; null lease_seconds / remaining_seconds means the fault has no automatic expiry; expired numeric leases do not prove agent repair."},
    {"name": "deactivate_demo_fault", "parameters": {
        "type": "object", "additionalProperties": False,
        "required": ["fault_id", "started_at"], "properties": {
            "fault_id": {"type": "string", "minLength": 1, "maxLength": 64},
            "started_at": {"type": "string", "minLength": 1, "maxLength": 100},
        }},
     "description": "Deactivate the previously observed demo fault using DELETE /api/demo-faults (no request body). Copy fault_id and started_at exactly from get_demo_faults in this session. Rechecks the active identity before DELETE and reads state afterward. Makes a real change only on the operator-configured local demo service. One DELETE attempt per session; a timeout has unknown outcome and must not be retried. The API has no atomic conditional delete, so this requires a single operator. active=null means fault control cleared, not checkout recovery; gather fresh observations afterward."},
    {"name": "check_shop_health", "parameters": EMPTY,
     "description": "Read GET /api/health from the configured local shop and return the actual response with observation time. HTTP errors remain errors. Health is a limited probe, not a checkout transaction or proof that database writes recovered."},
]


class DemoRepair:
    def __init__(self, base_url: str):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise ValueError("NIGHTWATCH_SHOP_URL must be a local HTTP origin for an exclusively operated demo")
        self.base_url = base_url.rstrip("/")
        self.validator = Draft202012Validator(json.loads(
            Path(__file__).with_name("demo-faults.schema.json").read_text()))
        self.observed: dict | None = None
        self.attempted = False
        self.started = time.monotonic()

    def _request(self, path: str, method: str = "GET") -> dict:
        try:
            with build_opener(NoRedirect).open(Request(self.base_url + path, method=method,
                    headers={"Accept": "application/json"}), timeout=2) as response:
                body = response.read(16385)
        except HTTPError as error:
            raise OSError(f"Shop {method} {path} returned HTTP {error.code}") from None
        except (URLError, TimeoutError, OSError):
            raise OSError(f"Shop {method} {path} failed; mutation outcome may be unknown; do not retry DELETE") from None
        if len(body) > 16384:
            raise ValueError("Shop response exceeds 16 KiB")
        try:
            result = json.loads(body)
            encode(result)
        except (ValueError, UnicodeError):
            raise ValueError("Shop response is not finite JSON") from None
        if not isinstance(result, dict):
            raise ValueError("Shop response must be a JSON object")
        if path == "/api/demo-faults" and next(self.validator.iter_errors(result), None) is not None:
            raise ValueError("Shop response does not match the published DemoFaultsResponse contract")
        if path == "/api/demo-faults" and "active" not in result:
            raise ValueError("Shop omitted active state; cannot establish fault clearance")
        return result

    def _query(self, name: str, args: dict) -> dict:
        if name == "check_shop_health":
            return self._request("/api/health")
        if name == "get_demo_faults":
            self.observed = None
            result = self._request("/api/demo-faults")
            self.observed = result.get("active")
            return result
        if name != "deactivate_demo_fault":
            raise ValueError("Unknown demo repair tool")
        if self.attempted:
            raise ValueError("DELETE already attempted in this session; read current state instead")
        if not self.observed or any(self.observed.get(k) != args[k] for k in ("fault_id", "started_at")):
            raise ValueError("Read this exact active fault with get_demo_faults before deactivation")
        before = self._request("/api/demo-faults")
        active = before.get("active")
        if not active or any(active.get(k) != args[k] for k in ("fault_id", "started_at")):
            self.observed = None
            raise ValueError("Active fault changed or expired; DELETE was not sent")
        remaining = active["remaining_seconds"]
        if remaining is not None and remaining <= 0:
            raise ValueError("Fault lease expired; DELETE was not sent")
        self.attempted = True
        response = self._request("/api/demo-faults", "DELETE")
        after = self._request("/api/demo-faults")
        return {"before": before, "response": response, "after": after,
                "delete_sent": True, "fault_control_cleared": response.get("active") is None and after.get("active") is None,
                "recovery_verified": False,
                "limitation": "Non-atomic GET/DELETE; concurrent replacement or lease expiry cannot be excluded. Fault-control clearance is not business recovery."}

    async def query(self, name: str, args: dict) -> Observation:
        result = await asyncio.to_thread(self._query, name, args)
        return Observation(result={"observed_at": datetime.now(timezone.utc).isoformat(), **result},
                           source="shop_demo_api", t=int(time.monotonic() - self.started),
                           summary_zh=f"已執行 {name}；此為店面演練 API 結果，業務恢復需獨立驗證。")
