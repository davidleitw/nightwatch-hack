"""Contract boundaries and framework control flow. All model calls here are stand-ins."""

import asyncio
import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jsonschema import Draft202012Validator
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from nightwatch_agent.loop import (
    Investigation, Limits, Observation, bounded_result, encode, investigate,
    make_tool, safe_error, static_instructions,
)
from nightwatch_agent.replay import Recording

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
SCHEMA = json.loads((CONTRACTS / "schemas/agent-report.schema.json").read_text())


class LoopChecks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.recording = Recording(CONTRACTS / "fixtures/catalog_pool_leak")

    async def invoke(self, model, backend=None, limits=None):
        return await investigate(
            model=model, backend=backend or self.recording.query,
            capabilities=self.recording.capabilities, opening=self.recording.opening,
            report_schema=SCHEMA, limits=limits,
        )

    async def usable_test_trace(self, name, args):
        observed = await self.recording.query(name, args)
        if name == "get_trace":
            # Deliberately synthetic, only in this test: production replay stays untouched.
            return Observation(
                {"path_kind": "error", "path": [{"service": "catalog", "span": "GetProduct", "duration_ms": 640, "error": True}],
                 "error_services": ["catalog"], "slow_services": [], "contained": []},
                "test", observed.t, "測試用 span",
            )
        return observed

    async def test_framework_round_trips_and_valid_report(self):
        result = await self.invoke(self.recording.scripted_model(), self.usable_test_trace)
        self.assertEqual(result.status, "report_ready", result.reason)
        self.assertEqual(result.report["root_cause"]["node"], "catalog")
        self.assertEqual([ev["id"] for ev in result.evidence], ["ev-0001", "ev-0002", "ev-0003", "ev-0004"])
        self.assertEqual(result.usage["requests"], 5)
        returns = [part for msg in result.messages if isinstance(msg, ModelRequest)
                   for part in msg.parts if isinstance(part, ToolReturnPart)]
        self.assertEqual(len(returns), 4)
        self.assertEqual(returns[0].content["evidence_id"], "ev-0001")

    async def test_recorded_empty_trace_cannot_pass_procedure(self):
        result = await self.invoke(self.recording.scripted_model())
        self.assertEqual(result.status, "unresolved")
        self.assertIn("procedure_incomplete", result.reason)
        self.assertIsNone(result.report)

    async def test_tool_boundaries_before_backend(self):
        cases = {
            "get_node_history": [("window_secs", 59), ("window_secs", 901), ("window_secs", True), ("node", "invented")],
            "find_traces": [("window_secs", 14), ("window_secs", 901), ("limit", 0), ("limit", 21), ("mode", "invented")],
            "get_trace": [("trace_id", "f" * 31)],
            "get_node_detail": [("node", "invented")],
        }
        for definition in self.recording.capabilities["tools"]:
            name = definition["name"]
            valid = next(call["args"] for call in self.recording.calls if call["tool"] == name)
            for key, value in cases[name]:
                with self.subTest(tool=name, key=key, value=value):
                    invoked = []

                    async def backend(tool, args):
                        invoked.append(args)
                        return Observation({}, "test", 0, "")

                    state = Investigation(backend, Limits())
                    result = await make_tool(definition).function(SimpleNamespace(deps=state), **{**valid, key: value})
                    self.assertIn("error", result)
                    self.assertEqual(invoked, [])
                    self.assertEqual(state.calls, 1)
                    self.assertEqual(state.evidence, [])

    async def test_valid_boundary_values_reach_backend(self):
        for definition in self.recording.capabilities["tools"]:
            if definition["name"] not in ("get_node_history", "find_traces"):
                continue
            name = definition["name"]
            valid = next(call["args"] for call in self.recording.calls if call["tool"] == name)
            for field, spec in definition["parameters"]["properties"].items():
                if "minimum" not in spec:
                    continue
                for value in (spec["minimum"], spec["maximum"]):
                    invoked = []

                    async def backend(tool, args):
                        invoked.append(args)
                        return Observation({}, "test", 0, "")

                    state = Investigation(backend, Limits())
                    result = await make_tool(definition).function(SimpleNamespace(deps=state), **{**valid, field: value})
                    self.assertNotIn("error", result)
                    self.assertEqual(len(invoked), 1)

    async def test_trace_id_must_come_from_find_traces(self):
        result = await self.invoke(FunctionModel(lambda messages, info: ModelResponse(parts=[
            ToolCallPart("get_trace", {"trace_id": "f" * 32}, tool_call_id="bad")
        ])), limits=Limits(max_tool_calls=1))
        self.assertEqual(result.evidence, [])
        errors = [event["payload"].get("error", "") for event in result.events]
        self.assertTrue(any("find_traces" in error for error in errors))

    async def test_bad_json_gets_one_retry(self):
        requests = []

        def respond(messages, info):
            requests.append(messages)
            return ModelResponse(parts=[TextPart("```json\n{}\n```")])

        result = await self.invoke(FunctionModel(respond))
        self.assertEqual(result.status, "unresolved")
        self.assertEqual(len(requests), 2)

    async def test_invented_evidence_is_rejected(self):
        original = self.recording.scripted_model()

        async def respond(messages, info):
            response = original.function(messages, info)
            for part in response.parts:
                if isinstance(part, TextPart):
                    part.content = part.content.replace("ev-0001", "ev-9999")
            return response

        result = await self.invoke(FunctionModel(respond), self.usable_test_trace)
        self.assertEqual(result.status, "unresolved")
        self.assertIsNone(result.report)

    async def test_budget_removes_tools_and_allows_final_answer(self):
        requests = 0

        def respond(messages, info):
            nonlocal requests
            requests += 1
            if requests == 1:
                return ModelResponse(parts=[ToolCallPart("get_node_history", {"node": "catalog", "window_secs": 900}, tool_call_id="one")])
            self.assertEqual(info.function_tools, [])
            return ModelResponse(parts=[TextPart("inconclusive: 預算不足以取得 trace")])

        result = await self.invoke(FunctionModel(respond), limits=Limits(max_tool_calls=1))
        self.assertEqual(result.status, "unresolved")
        self.assertEqual(len(result.evidence), 1)

    async def test_wall_timeout_and_token_budget(self):
        async def slow(messages, info):
            await asyncio.sleep(1)
            return ModelResponse(parts=[TextPart("inconclusive: 沒資料")])

        timed = await self.invoke(FunctionModel(slow), limits=Limits(max_secs=0.02))
        self.assertEqual(timed.status, "budget_exhausted")
        tokens = await self.invoke(self.recording.scripted_model(), limits=Limits(max_tokens=1))
        self.assertEqual(tokens.status, "budget_exhausted")

    def test_schema_example_and_stable_prefix(self):
        Draft202012Validator(SCHEMA).validate(json.loads((CONTRACTS / "examples/agent-report.json").read_text()))
        prefix = static_instructions(self.recording.capabilities, SCHEMA)
        self.assertEqual(prefix, static_instructions(copy.deepcopy(self.recording.capabilities), SCHEMA))
        self.assertNotIn("inc-1789003745", prefix)
        self.assertNotIn("2026-09-10", prefix)
        self.assertNotIn("truth", encode(self.recording.opening))
        self.assertNotIn("hypothesis", encode(self.recording.opening))

    def test_truncation_and_secret_redaction(self):
        raw = {"points": [[i, "測試" * 100] for i in range(100)]}
        bounded = bounded_result(raw, 800)
        self.assertLessEqual(len(encode(bounded).encode()), 800)
        self.assertTrue(bounded["truncated"])
        self.assertEqual(bounded["points"][0][0], 0)
        self.assertEqual(bounded["points"][-1][0], 99)
        self.assertEqual(len(raw["points"]), 100)
        with patch.dict("os.environ", {"NIGHTWATCH_LLM_API_KEY": "test-secret-value"}):
            self.assertNotIn("test-secret-value", safe_error(ValueError("failed test-secret-value")))


if __name__ == "__main__":
    unittest.main()
