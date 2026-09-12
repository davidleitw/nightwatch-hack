"""Exercise the real shop, HTTP investigation API and configured model (no substitutes).

Requires an exclusively operated local demo shop; creates two demo orders.
Run with the existing server virtualenv and model environment already loaded.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def request(base, path, method="GET", body=None):
    req = Request(base + path, method=method,
                  data=None if body is None else json.dumps(body).encode(),
                  headers={"Content-Type": "application/json"})
    try:
        response = urlopen(req, timeout=15)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read()
        if not raw:
            body = None
        elif response.headers.get_content_type() == "application/json":
            body = json.loads(raw)
        else:
            body = {"raw_response": raw.decode(errors="replace")}
        return response.status, body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shop-url", required=True)
    parser.add_argument("--graph-url", required=True)
    parser.add_argument("--port", type=int, default=8017)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    status, initial = request(args.shop_url, "/api/demo-faults")
    assert status == 200 and initial.get("active") is None, "Demo shop must have no active fault"
    env = dict(os.environ, NIGHTWATCH_SHOP_URL=args.shop_url,
               NIGHTWATCH_GRAPH_URL=args.graph_url,
               NIGHTWATCH_INVESTIGATION_DB=str(args.output / "investigations.sqlite3"),
               PYDANTIC_AI_NO_BANNER="1")
    code = ("from fastapi import FastAPI; from investigation_api import install_investigations; "
            "import uvicorn; app=FastAPI(); install_investigations(app); "
            f"uvicorn.run(app,host='127.0.0.1',port={args.port},log_level='warning')")
    base = f"http://127.0.0.1:{args.port}"
    log = (args.output / "server.log").open("w")
    process = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=log, stderr=log)
    owned_fault = None
    carts = []
    summaries = []
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("Verification server exited; inspect server.log")
            try:
                if request(base, "/api/investigations/state")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Verification server did not start")
        status, products = request(args.shop_url, "/api/products")
        assert status == 200 and products
        for fault_id in ("checkout_exception", "database_write_lock"):
            status, cart = request(args.shop_url, "/api/carts", "POST")
            assert status == 201
            cart_id = cart["id"]
            carts.append(cart_id)
            status, _ = request(args.shop_url, f"/api/carts/{cart_id}/items", "POST",
                                {"product_id": products[0]["id"], "quantity": 1})
            assert status == 200
            status, fault = request(args.shop_url, "/api/demo-faults", "POST", {"fault_id": fault_id})
            assert status == 200 and fault["active"]["fault_id"] == fault_id
            owned_fault = fault["active"]
            shipping = {"name": "NightWatch local repair verification", "address": "Local demo verification only"}
            before_status, before = request(args.shop_url, f"/api/carts/{cart_id}/checkout", "POST", shipping)
            assert before_status >= 500, (fault_id, before_status, before)
            print(json.dumps({"fault": fault_id, "checkout_before": before_status}), flush=True)
            status, created = request(base, "/api/investigations", "POST",
                                      {"request_id": uuid4().hex, "trigger": {"source": "manual", "reason": "Local demo fault repair verification"}})
            assert status == 202
            inv = created["investigation_id"]
            deadline = time.monotonic() + 240
            seen = set()
            while time.monotonic() < deadline:
                status, detail = request(base, f"/api/investigations/{inv}")
                assert status == 200
                _, events = request(base, f"/api/investigations/{inv}/events?limit=100")
                for event in events.get("items", []):
                    if event["seq"] not in seen and event["type"] in {"tool.started", "tool.failed"}:
                        print(json.dumps({"investigation": inv, "event": event["type"], "tool": event["payload"].get("tool"), "error": event["payload"].get("error")}), flush=True)
                        seen.add(event["seq"])
                if detail["status"] != "running":
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Investigation timed out")
            _, exported = request(base, f"/api/investigations/{inv}/export")
            (args.output / f"{fault_id}.export.json").write_text(json.dumps(exported, ensure_ascii=False, indent=2))
            assert exported["complete"] and detail["report"]["investigation_report"], "No complete model report"
            evidence = detail["evidence"]
            actions = [e for e in evidence if e["tool"] == "deactivate_demo_fault"]
            assert detail["status"] == "completed" and actions, detail
            action = actions[-1]["result"]
            assert action["delete_sent"] and action["fault_control_cleared"], action
            remaining = action["before"]["active"]["remaining_seconds"]
            assert remaining is None or remaining > 0
            status, state = request(args.shop_url, "/api/demo-faults")
            assert status == 200 and state.get("active") is None
            owned_fault = None
            after_status, after = request(args.shop_url, f"/api/carts/{cart_id}/checkout", "POST", shipping)
            assert after_status == 201 and after["id"], (after_status, after)
            summary = {"fault": fault_id, "investigation_id": inv, "status": detail["status"],
                       "checkout_before": before_status, "checkout_after": after_status,
                       "order_id": after["id"], "remaining_seconds_before_delete": action["before"]["active"]["remaining_seconds"],
                       "tools": [e["tool"] for e in evidence], "usage": detail["usage"]}
            summaries.append(summary)
            (args.output / "summary.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2))
            print(json.dumps(summary, ensure_ascii=False), flush=True)
    finally:
        try:
            if owned_fault:
                status, current = request(args.shop_url, "/api/demo-faults")
                active = current.get("active")
                if status == 200 and active and all(active.get(k) == owned_fault[k] for k in ("fault_id", "started_at")):
                    print("Cleanup owned demo fault", request(args.shop_url, "/api/demo-faults", "DELETE")[0], flush=True)
            for cart_id in carts:
                print("Cleanup verification cart", request(args.shop_url, f"/api/carts/{cart_id}", "DELETE")[0], flush=True)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()
            print("Verification server stopped", flush=True)


if __name__ == "__main__":
    main()
