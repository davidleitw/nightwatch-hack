"""Explicit recording replay, never used as a fallback for a failed live query."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from .loop import Json, Observation, TOOL_CAPS, encode


class Recording:
    def __init__(self, directory: Path):
        state = json.loads((directory / "state.json").read_text())
        incident = state["incident"]
        evidence = {item["id"]: item for item in incident["evidence"]}
        started = {}
        self.calls: list[Json] = []
        detected_at = datetime.fromisoformat(incident["detected_at"].replace("Z", "+00:00"))
        with (directory / "events.jsonl").open() as events:
            for line in events:
                envelope = json.loads(line)
                if envelope.get("event") != "incident":
                    continue
                event = envelope["data"]["event"]
                payload = event.get("payload", {})
                if event["type"] == "tool.started":
                    started[payload["call_id"]] = payload
                elif event["type"] == "observation.recorded":
                    call = started.get(payload["call_id"])
                    for evidence_id in payload.get("evidence_ids", []):
                        item = evidence.get(evidence_id)
                        if call and item and call["tool"] in TOOL_CAPS:
                            at = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
                            self.calls.append({
                                "tool": call["tool"], "args": call["args"], "old_id": evidence_id,
                                "observation": Observation(
                                    result=item["observed_value"], source="recording:" + item["source"],
                                    t=int((at - detected_at).total_seconds()), summary_zh=item["summary"],
                                ),
                            })
        if not self.calls:
            raise ValueError("錄影沒有可用的工具往返")
        self.capabilities = copy.deepcopy(state["capabilities"])
        self.capabilities["tools"] = [
            definition for definition in self.capabilities["tools"]
            if any(call["tool"] == definition["name"] for call in self.calls)
        ]
        for definition in self.capabilities["tools"]:
            queries = [call["args"] for call in self.calls if call["tool"] == definition["name"]]
            definition["recorded_queries"] = queries
        with (directory / "snapshots.jsonl").open() as snapshots:
            before = [json.loads(line) for line in snapshots]
        before = [snapshot for snapshot in before if snapshot["t"] <= 0]
        if not before:
            raise ValueError("錄影沒有偵測前的快照")
        current = max(before, key=lambda snapshot: snapshot["t"])
        fields = ("id", "kind", "traffic", "errors", "p95_ms", "saturation", "alive", "status", "trend")
        # Do not pass closed-incident state, the old hypothesis, faults or truth to a real model.
        self.opening = {
            "mode": "recorded_observations",
            "time_reference": {"kind": "incident_detection", "at": incident["detected_at"]},
            "detection": incident["detection"],
            "pinned_window": {"from_t": min(s["t"] for s in before), "to_t": current["t"], "count": len(before)},
            "nodes": [{key: node.get(key) for key in fields} for node in current["nodes"]],
            "edges": [{key: edge[key] for key in ("from", "to", "kind")} for edge in current["edges"]],
        }
        self._recorded_report = incident["hypothesis"]

    async def query(self, name: str, args: Json) -> Observation:
        for call in self.calls:
            if call["tool"] == name and call["args"] == args:
                return copy.deepcopy(call["observation"])
        raise LookupError("This query was not captured in the recording; no result can be inferred or fabricated")

    def scripted_model(self) -> FunctionModel:
        """Exercise framework dispatch with recorded calls, not model reasoning."""
        position = 0
        remapped = encode(self._recorded_report)
        for index, call in enumerate(self.calls, 1):
            remapped = remapped.replace(call["old_id"], f"ev-{index:04d}")

        def respond(messages, info):
            nonlocal position
            if position < len(self.calls):
                call = self.calls[position]
                position += 1
                return ModelResponse(parts=[ToolCallPart(call["tool"], call["args"], tool_call_id=f"replay-{position}")])
            return ModelResponse(parts=[TextPart(remapped)])

        return FunctionModel(respond)
