"""Black-box frontend acceptance against an isolated investigation API.

The target must use a disposable database and a test-injected model that waits at
least seven seconds before returning. This script performs real HTTP/SSE requests
and creates two investigations; it never supplies a model or changes server config.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def request(base, path, body=None, headers=None):
    payload = None if body is None else json.dumps(body).encode()
    req = Request(base + path, data=payload, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        response = urlopen(req, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        if path.startswith("/api/investigations"):
            assert "no-store" in response.headers.get("Cache-Control", ""), (path, response.headers)
        return response.status, json.load(response)


def poll(fn, predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if predicate(value):
            return value
        time.sleep(0.1)
    raise AssertionError("Timed out waiting for API state")


class Stream:
    """Small SSE consumer: state owns active ID; events only populate history."""

    def __init__(self, base, headers=None, after=None):
        self.url = base + "/api/investigations/stream" + ("" if after is None else f"?after={after}")
        self.headers = headers or {}
        self.frames = []
        self.error = None
        self.active = None
        self.state_cursor = -1
        self.activity = {}
        self.condition = threading.Condition()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            with urlopen(Request(self.url, headers=self.headers), timeout=12) as response:
                assert response.headers.get_content_type() == "text/event-stream"
                assert "no-store" in response.headers.get("Cache-Control", "")
                name, data, event_id = "message", [], None
                while not self.stop.is_set():
                    line = response.readline()
                    if not line:
                        break
                    line = line.decode().rstrip("\r\n")
                    if line == "":
                        if data:
                            parsed = json.loads("\n".join(data))
                            with self.condition:
                                if name == "state" and parsed["cursor"] >= self.state_cursor:
                                    self.state_cursor = parsed["cursor"]
                                    self.active = parsed["active_investigation_id"]
                                if name == "investigation":
                                    assert event_id == str(parsed["cursor"])
                                    self.activity[(parsed["investigation_id"], parsed["seq"])] = parsed
                                else:
                                    assert event_id is None, "Only investigation events should carry SSE id"
                                self.frames.append((name, parsed, time.monotonic()))
                                self.condition.notify_all()
                        name, data, event_id = "message", [], None
                    elif line.startswith("event:"):
                        name = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif line.startswith("id:"):
                        event_id = line[3:].strip()
        except Exception as error:
            if not self.stop.is_set():
                with self.condition:
                    self.error = error
                    self.condition.notify_all()

    def wait(self, predicate, timeout=25):
        deadline = time.monotonic() + timeout
        with self.condition:
            while time.monotonic() < deadline:
                if self.error:
                    raise self.error
                if predicate(self.frames):
                    return list(self.frames)
                self.condition.wait(min(0.25, max(0, deadline - time.monotonic())))
        raise AssertionError("Timed out waiting for SSE event")

    def close(self):
        self.stop.set()
        self.thread.join(timeout=4)
        assert not self.thread.is_alive(), "SSE reader did not stop after a heartbeat"


def has_event(frames, investigation_id, event_type):
    return any(name == "investigation" and data["investigation_id"] == investigation_id
               and data["type"] == event_type for name, data, _ in frames)


def run(base, expected_outcome):
    prefix = "/api/investigations"
    checks = []
    streams = []
    initial = poll(lambda: request(base, prefix + "/state")[1], lambda s: s.get("graph") is not None)
    assert initial["active_investigation_id"] is None
    assert initial["active_investigation"] is None
    assert initial["schema_version"] == "nightwatch.investigation-state.v1"
    original_graph = initial["graph"]
    assert all(node["assessment"] == "unassessed" for node in original_graph["nodes"])
    _, openapi = request(base, "/openapi.json")
    for path, methods in {
        prefix: ["get", "post"], prefix + "/state": ["get"], prefix + "/stream": ["get"],
        prefix + "/{id}": ["get"], prefix + "/{id}/events": ["get"], prefix + "/{id}/context": ["get"],
    }.items():
        for method in methods:
            assert method in openapi["paths"][path], (path, method)
            if path.endswith("/stream"):
                continue
            success = "202" if method == "post" else "200"
            schema = openapi["paths"][path][method]["responses"][success]["content"]["application/json"]["schema"]
            assert "$ref" in schema, ("Response must expose a named schema", path, method, schema)
            definition = openapi["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]]
            assert definition.get("properties"), (path, method, definition)
    checks.append("typed routes registered in the shared OpenAPI")

    try:
        first_stream = Stream(base)
        second_stream = Stream(base)
        streams.extend([first_stream, second_stream])
        for stream in streams:
            frames = stream.wait(lambda f: any(name == "state" for name, _, _ in f))
            assert frames[0][0] == "state"
            stream.wait(lambda f: any(name == "graph" for name, _, _ in f))
        checks.append("two subscribers receive initial state and graph while idle")

        first_body = {"request_id": "acceptance-" + uuid4().hex,
                      "trigger": {"source": "manual", "reason": "Frontend acceptance session one"}}
        started_at = time.monotonic()
        code, accepted = request(base, prefix, first_body)
        assert code == 202, accepted
        assert time.monotonic() - started_at < 2, "POST waited for investigation execution"
        first_id = accepted["investigation_id"]
        code, duplicate = request(base, prefix, first_body)
        assert code == 202 and duplicate == accepted
        changed = {**first_body, "trigger": {"source": "manual", "reason": "changed request"}}
        code, conflict = request(base, prefix, changed)
        assert code == 409 and conflict["error"]["code"] == "request_conflict"
        second_body = {"request_id": "acceptance-" + uuid4().hex,
                       "trigger": {"source": "manual", "reason": "Frontend acceptance session two"}}
        code, busy = request(base, prefix, second_body)
        assert code == 409 and busy["error"]["code"] == "investigation_active"
        assert busy["error"]["details"]["active_investigation_id"] == first_id
        checks.append("immediate creation, durable request identity, conflict and busy semantics")

        first_stream.wait(lambda f: any(name == "state" and data["active_investigation_id"] == first_id for name, data, _ in f))
        first_stream.wait(lambda f: any(name == "graph" and at > started_at for name, _, at in f))
        assert request(base, prefix + "/state")[1]["active_investigation_id"] == first_id, "Use a test runner that waits at least seven seconds"
        first_stream.wait(lambda f: has_event(f, first_id, "investigation.finished"))
        second_stream.wait(lambda f: has_event(f, first_id, "investigation.finished"))
        code, detail = request(base, prefix + "/" + first_id)
        assert code == 200 and detail["report"] is not None
        assert detail["outcome"] == expected_outcome, detail
        assert detail["report"]["investigation_id"] == first_id
        assert detail["report"]["summary_zh"]
        assert detail["evidence"] and detail["context_complete"]
        code, context = request(base, prefix + "/" + first_id + "/context")
        assert code == 200 and context["complete"] is True
        assert context["context"]["instructions"] and context["context"]["messages"]
        assert [tool["name"] for tool in context["context"]["tools"]] == ["get_graph"]
        assert all("assessment" not in node for evidence in detail["evidence"] if evidence["tool"] == "get_graph" for node in evidence["result"]["nodes"])
        idle = poll(lambda: request(base, prefix + "/state")[1], lambda s: s["active_investigation_id"] is None)
        assert idle["last_completed_investigation_id"] == first_id
        assert idle["graph"] == original_graph
        checks.append("graph continues during execution; finished event has durable report/context and clears only active session")

        code, accepted_second = request(base, prefix, second_body)
        assert code == 202, accepted_second
        second_id = accepted_second["investigation_id"]
        assert second_id != first_id
        replay = Stream(base, headers={"Last-Event-ID": str(initial["cursor"])})
        streams.append(replay)
        replay.wait(lambda f: has_event(f, first_id, "investigation.finished"))
        assert replay.frames[0][0] == "state"
        assert replay.frames[0][1]["active_investigation_id"] == second_id
        assert replay.active == second_id, "Replaying old completion cleared the new investigation"
        checks.append("old events replay after current state without clearing a new active investigation")
        replay.close()
        streams.remove(replay)
        first_stream.wait(lambda f: has_event(f, second_id, "investigation.finished"))
        second_stream.wait(lambda f: has_event(f, second_id, "investigation.finished"))
        finished_at = time.monotonic()
        first_stream.wait(lambda f: any(name == "graph" and at > finished_at for name, _, at in f), timeout=8)
        first_stream.wait(lambda f: any(name == "ping" and at > finished_at for name, _, at in f), timeout=5)
        first_stream.wait(lambda f: any(name == "state" and data["active_investigation_id"] is None
                                      and data["last_completed_investigation_id"] == second_id for name, data, _ in f))
        checks.append("disconnect does not cancel; graph and ping continue after completion")

        _, second_context = request(base, prefix + "/" + second_id + "/context")
        _, second_detail = request(base, prefix + "/" + second_id)
        assert second_context["complete"] is True
        assert first_body["trigger"]["reason"] not in json.dumps(second_context["context"], ensure_ascii=False)
        assert second_detail["evidence"][0]["id"] == "ev-0001"
        assert second_detail["report"]["investigation_id"] == second_id

        cursors = []
        for stream in (first_stream, second_stream):
            values = [data["cursor"] for name, data, _ in stream.frames if name == "investigation"]
            assert values == sorted(set(values))
            cursors.append(values)
        assert cursors[0] == cursors[1], "Subscribers did not receive the same durable events"
        events, after = [], 0
        while True:
            code, page = request(base, prefix + f"/{first_id}/events?after={after}&limit=2")
            assert code == 200
            events.extend(page["items"])
            if page["next_after"] is None:
                break
            assert page["next_after"] > after
            after = page["next_after"]
        assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
        starts = {event["payload"]["call_id"] for event in events if event["type"] == "tool.started"}
        ends = {event["payload"]["call_id"] for event in events if event["type"] in ("observation.recorded", "tool.failed")}
        assert starts == ends and starts
        assert sum(event["type"] == "investigation.finished" for event in events) == 1
        _, second_events = request(base, prefix + f"/{second_id}/events")
        second_calls = {event["payload"]["call_id"] for event in second_events["items"]
                        if event["type"] == "tool.started"}
        assert second_calls and not (starts & second_calls)
        _, page_one = request(base, prefix + "?limit=1")
        assert page_one["items"][0]["id"] == second_id
        _, page_two = request(base, prefix + f"?limit=1&before={page_one['next_before']}")
        assert page_two["items"][0]["id"] == first_id
        code, old_duplicate = request(base, prefix, first_body)
        assert code == 202 and old_duplicate == accepted
        assert request(base, prefix + "/state")[1]["active_investigation_id"] is None
        checks.append("two-client delivery, independent session context, tool pairing, pagination and completed-request replay")

        for path in (prefix + "?limit=0", prefix + "?limit=1&limit=2", prefix + "/state?unknown=1",
                     prefix + "/stream?after=-1", prefix + "/stream?after=999999999999999",
                     prefix + f"/{first_id}/events?limit=0"):
            code, error = request(base, path)
            assert code == 400 and "error" in error, (path, code, error)
        code, _ = request(base, prefix + "/stream?after=0", headers={"Last-Event-ID": "1"})
        assert code == 400
        code, _ = request(base, prefix + "/does-not-exist")
        assert code == 404
        code, _ = request(base, prefix, {"request_id": "bad-extra-" + uuid4().hex, "extra": True})
        assert code == 400
        checks.append("invalid requests/cursors fail before streaming; unknown sessions return 404")
        print(json.dumps({"checks_passed": checks, "investigations": [first_id, second_id],
                          "model": "server-injected test model", "data": "real HTTP to the synthetic graph API",
                          "live_model_reasoning_verified": False}, ensure_ascii=False, indent=2))
    finally:
        for stream in streams:
            stream.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Isolated test API with disposable storage and a delayed test model")
    parser.add_argument("--expect-outcome", default="unresolved")
    args = parser.parse_args()
    run(args.base_url.rstrip("/"), args.expect_outcome)
