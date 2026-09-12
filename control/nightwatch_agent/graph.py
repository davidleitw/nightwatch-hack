"""Read and project the documented graph API into model-visible observations."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from jsonschema import Draft202012Validator

from .loop import Json, Observation, encode


GRAPH_TOOL = {
    "name": "get_graph",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}

TIMESTAMP = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]([01]\d|2[0-3]):[0-5]\d)$"}
SNAPSHOTS_TOOL = {
    "name": "list_graph_snapshots",
    "parameters": {"type": "object", "properties": {
        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        "before_seq": {"type": "integer", "minimum": 1},
    }, "additionalProperties": False},
}
INDEX_SCHEMA = {
    "type": "object", "required": ["snapshots", "next_before_seq"],
    "properties": {
        "snapshots": {"type": "array", "maxItems": 500, "items": {
            "type": "object", "required": ["seq", "at"], "properties": {
                "seq": {"type": "integer", "minimum": 1}, "at": TIMESTAMP,
            }, "additionalProperties": False,
        }},
        "next_before_seq": {"type": ["integer", "null"], "minimum": 1},
    }, "additionalProperties": False,
}


def timestamp(value: str) -> datetime:
    if not re.fullmatch(TIMESTAMP["pattern"], value):
        raise ValueError("timestamp must be RFC3339 with an explicit timezone")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GraphAPI:
    def __init__(self, url: str, schema_dir: Path):
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Graph URL must be an HTTP(S) endpoint without credentials or a fragment")
        self.url = url
        self.live = not parsed.query
        self.started = time.monotonic()
        self.started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        # Resolve the contract's two local references without network schema loading.
        schema = json.loads((schema_dir / "snapshot.schema.json").read_text())
        for collection, filename in (("nodes", "node.schema.json"), ("edges", "edge.schema.json")):
            schema["properties"][collection]["items"] = json.loads((schema_dir / filename).read_text())
        self.validator = Draft202012Validator(schema)

    def _request(self, url: str) -> Json:
        try:
            request = Request(url, headers={"Accept": "application/json"})
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
        return raw

    def _read(self, at: str | None = None) -> Json:
        if at is not None:
            timestamp(at)
            if not self.live:
                raise ValueError("Historical queries cannot be mixed with an operator-selected graph query")
        raw = self._request(self.url if at is None else self.url + "?" + urlencode({"timestamp": at}))
        if next(self.validator.iter_errors(raw), None) is not None:
            # Never echo a rejected payload (which may include assessment or secrets).
            raise ValueError("Graph response does not match the snapshot contract")
        observed_at = timestamp(raw["at"])
        if at is not None and observed_at > timestamp(at):
            raise ValueError("Historical API returned a snapshot later than the requested timestamp")
        node_ids = [node["id"] for node in raw["nodes"]]
        if any(not node_id for node_id in node_ids) or len(node_ids) != len(set(node_ids)):
            raise ValueError("Graph node IDs must be nonempty and unique")
        if any(edge["from"] not in node_ids or edge["to"] not in node_ids for edge in raw["edges"]):
            raise ValueError("Graph edge references an unknown node")
        graph = copy.deepcopy(raw)
        for node in graph["nodes"]:
            del node["assessment"]
        return graph

    def _snapshots(self, args: Json) -> Json:
        parsed = urlsplit(self.url)
        url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/snapshots", urlencode(args), ""))
        result = self._request(url)
        if next(Draft202012Validator(INDEX_SCHEMA).iter_errors(result), None) is not None:
            raise ValueError("Snapshot index does not match the Guard Room API contract")
        rows = result["snapshots"]
        seqs = [row["seq"] for row in rows]
        if len(rows) > args.get("limit", 100) or seqs != sorted(set(seqs), reverse=True):
            raise ValueError("Snapshot index must contain unique sequences in descending order within limit")
        if "before_seq" in args and any(seq >= args["before_seq"] for seq in seqs):
            raise ValueError("Snapshot index ignored before_seq")
        if result["next_before_seq"] is not None and (not seqs or result["next_before_seq"] != seqs[-1]):
            raise ValueError("Snapshot index contains an invalid pagination cursor")
        for row in rows:
            timestamp(row["at"])
        return result

    async def prepare(self) -> None:
        graph = await asyncio.to_thread(self._read)
        definitions = [copy.deepcopy(GRAPH_TOOL)]
        if self.live:
            definitions[0]["parameters"]["properties"]["timestamp"] = copy.deepcopy(TIMESTAMP)
            definitions.extend([copy.deepcopy(SNAPSHOTS_TOOL), {
                "name": "get_node_detail", "parameters": {
                    "type": "object", "required": ["node"], "properties": {
                        "node": {"type": "string", "enum": [node["id"] for node in graph["nodes"]]},
                    }, "additionalProperties": False,
                },
            }])
        self.capabilities = {
            "nodes": [{"id": node["id"], "kind": node["kind"]} for node in graph["nodes"]],
            "tools": definitions,
        }
        self.opening = {
            "mode": "graph_api",
            "task": "Inspect the current graph, compare relevant retained snapshots when available, and investigate candidate nodes. Explain observed changes, hypotheses, counterevidence, limitations and the next evidence needed.",
            "time_reference": {"kind": "investigation_start", "at": self.started_at},
            "data_scope": (
                "Guard Room monitor graph and retained graph snapshots via HTTP. No incident has been detected by this runner. "
                "History defaults to one snapshot per 5 seconds retained for 15 minutes; outages are not backfilled. "
                "Historical graph snapshots are not the legacy get_node_history baseline series. No baseline is supplied. "
                "Error-log search, traces, Prometheus queries, health probes and runtime operations are not connected. "
                "A missing history endpoint is an explicit API failure, not an empty history."
                if self.live else
                "Operator-selected graph query: it may be a synthetic demo or a fixed historical snapshot. "
                "Only get_graph is exposed; do not mix it with live monitoring or other history."
            ),
        }

    async def query(self, name: str, args: Json) -> Observation:
        definition = next((tool for tool in self.capabilities["tools"] if tool["name"] == name), None)
        if definition is None:
            raise ValueError("Tool is not available for this graph source")
        if next(Draft202012Validator(definition["parameters"]).iter_errors(args), None) is not None:
            raise ValueError("Invalid arguments for graph tool")
        if name == "list_graph_snapshots":
            result = await asyncio.to_thread(self._snapshots, args)
            return Observation(result=result, source="guardroom_snapshot_index", t=int(time.monotonic() - self.started),
                               summary_zh=f"已讀取 {len(result['snapshots'])} 筆歷史快照索引；索引不含量測。")
        graph = await asyncio.to_thread(self._read, args.get("timestamp"))
        result = graph
        summary = f"已讀取 graph snapshot seq={graph['seq']} at={graph['at']}：{len(graph['nodes'])} 個節點、{len(graph['edges'])} 條邊。"
        if name == "get_node_detail":
            node = next((node for node in graph["nodes"] if node["id"] == args["node"]), None)
            if node is None:
                raise LookupError("Node no longer exists in the current graph")
            result = {
                "node": node["id"], "kind": node["kind"],
                "t": int((timestamp(graph["at"]) - timestamp(self.started_at)).total_seconds()),
                "now": {key: node[key] for key in ("traffic", "errors", "p95_ms", "saturation", "alive")},
                "extras": node["extras"], "checks": node["checks"], "logs_indexed": node["logs_indexed"],
                "edges_out": [edge for edge in graph["edges"] if edge["from"] == node["id"]],
                "edges_in": [edge for edge in graph["edges"] if edge["to"] == node["id"]],
            }
            summary = f"已讀取節點 {node['id']} 的量測與進出邊；snapshot seq={graph['seq']} at={graph['at']}；未接入 span 查詢。"
        return Observation(
            result=result,
            source="guardroom_graph_api",
            t=int(time.monotonic() - self.started),
            summary_zh=summary,
        )
