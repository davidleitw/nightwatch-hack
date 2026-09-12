"""Read and project the documented graph API into model-visible observations."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from jsonschema import Draft202012Validator

from .loop import Json, Observation, encode


GRAPH_TOOL = {
    "name": "get_graph",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GraphAPI:
    def __init__(self, url: str, schema_dir: Path):
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Graph URL must be an HTTP(S) endpoint without credentials or a fragment")
        self.url = url
        self.started = time.monotonic()
        self.started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        # Resolve the contract's two local references without network schema loading.
        schema = json.loads((schema_dir / "snapshot.schema.json").read_text())
        for collection, filename in (("nodes", "node.schema.json"), ("edges", "edge.schema.json")):
            schema["properties"][collection]["items"] = json.loads((schema_dir / filename).read_text())
        self.validator = Draft202012Validator(schema)

    def _read(self) -> Json:
        try:
            request = Request(self.url, headers={"Accept": "application/json"})
            with build_opener(NoRedirect).open(request, timeout=5) as response:
                body = response.read(512 * 1024 + 1)
        except HTTPError as error:
            raise OSError(f"Graph API returned HTTP {error.code}") from None
        except (URLError, TimeoutError, OSError):
            raise OSError("Graph API is unreachable or timed out") from None
        if len(body) > 512 * 1024:
            raise ValueError("Graph response exceeds the 512 KiB HTTP limit")
        try:
            raw = json.loads(body)
            encode(raw)  # Reject NaN and Infinity as well as malformed JSON.
        except (ValueError, UnicodeError):
            raise ValueError("Graph API did not return valid finite JSON") from None
        if next(self.validator.iter_errors(raw), None) is not None:
            # Never echo a rejected payload (which may include assessment or secrets).
            raise ValueError("Graph response does not match the snapshot contract")
        node_ids = [node["id"] for node in raw["nodes"]]
        if any(not node_id for node_id in node_ids) or len(node_ids) != len(set(node_ids)):
            raise ValueError("Graph node IDs must be nonempty and unique")
        if any(edge["from"] not in node_ids or edge["to"] not in node_ids for edge in raw["edges"]):
            raise ValueError("Graph edge references an unknown node")
        graph = copy.deepcopy(raw)
        for node in graph["nodes"]:
            del node["assessment"]
        return graph

    async def prepare(self) -> None:
        graph = await asyncio.to_thread(self._read)
        self.capabilities = {
            "nodes": [{"id": node["id"], "kind": node["kind"]} for node in graph["nodes"]],
            "tools": [copy.deepcopy(GRAPH_TOOL)],
        }
        self.opening = {
            "mode": "graph_api",
            "task": "Call get_graph to inspect the system. Explain observed symptoms, evidence limitations and what should be investigated next.",
            "time_reference": {"kind": "investigation_start", "at": self.started_at},
            "data_scope": "The configured API currently serves synthetic demo snapshots. No incident has been detected by this runner. Only graph queries are available; history, logs, traces and runtime operations are not connected.",
        }

    async def query(self, name: str, args: Json) -> Observation:
        if name != "get_graph" or args:
            raise ValueError("This backend supports only get_graph with no arguments")
        graph = await asyncio.to_thread(self._read)
        return Observation(
            result=graph,
            source="guardroom_graph_api",
            t=int(time.monotonic() - self.started),
            summary_zh=f"已讀取 graph snapshot：{len(graph['nodes'])} 個節點、{len(graph['edges'])} 條邊；來源健康依回傳欄位判讀。",
        )
