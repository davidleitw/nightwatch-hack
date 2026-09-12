"""PydanticAI owns the model/tool loop; this module owns NightWatch's boundaries."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from jsonschema import Draft202012Validator, ValidationError
from openai import APIError
from pydantic_ai import Agent, ModelRetry, RunContext, Tool, ToolOutput
from pydantic_ai.exceptions import ModelAPIError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from .prompts import GUARDROOM_PROMPT, GRAPH_SYSTEM_PROMPT, SYSTEM_PROMPT, tool_description
from .report import InvestigationReportBody, REPORT_VERSION, SUBMIT_DESCRIPTION, submit_definition, validate_submission

Json = dict[str, Any]
ContextCallback = Callable[[Json], None]
TOOL_CAPS = {
    "get_graph": 16384,
    "list_graph_snapshots": 65536,
    "get_node_history": 3072,
    "get_node_detail": 2048,
    "find_traces": 4096,
    "get_trace": 8192,
    "search_logs": 16384,
    "query_metric": 1024,
    "run_health_check": 2048,
    "inspect_runtime": 6144,
    "list_errors": 8192,
    "get_node_errors": 6144,
}


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def safe_error(error: Exception) -> str:
    text = str(error)
    for name, value in os.environ.items():
        if value and (name.endswith("API_KEY") or name.endswith("TOKEN")):
            text = text.replace(value, "[REDACTED]")
    return f"{type(error).__name__}: {text[:200]}"


def bounded_result(result: Json, cap: int) -> Json:
    """Keep valid JSON and useful rows; never mark an empty placeholder as evidence."""
    result = copy.deepcopy(result)
    while len(encode(result).encode()) > cap:
        result["truncated"] = True
        candidates: list[tuple[int, Any, Any]] = []

        def visit(value: Any) -> None:
            if isinstance(value, (dict, list)):
                items = value.items() if isinstance(value, dict) else enumerate(value)
                for key, child in items:
                    if isinstance(child, list) and len(child) > 2:
                        candidates.append((len(encode(child)), value, key))
                    elif isinstance(child, str) and len(child) > 160:
                        candidates.append((len(child), value, key))
                    visit(child)

        visit(result)
        if not candidates:
            return {"error": "tool_result_too_large", "truncated": True}
        _, parent, key = max(candidates, key=lambda item: item[0])
        value = parent[key]
        if isinstance(value, list):
            # Retain both ends of histories so onset is not silently shifted.
            parent[key] = value[::2] + ([] if (len(value) - 1) % 2 == 0 else [value[-1]])
        else:
            parent[key] = value[: len(value) // 2] + "…"
    return result


@dataclass(frozen=True)
class Observation:
    result: Json
    source: str
    t: int
    summary_zh: str


# The data owner injects a read-only implementation. No invented HTTP /tools endpoint.
Backend = Callable[[str, Json], Awaitable[Observation]]


@dataclass(frozen=True)
class Limits:
    max_tool_calls: int = 20
    max_secs: float = 900
    max_tokens: int = 400_000
    request_timeout_secs: float = 60

    def __post_init__(self) -> None:
        if min(self.max_tool_calls, self.max_secs, self.max_tokens, self.request_timeout_secs) <= 0:
            raise ValueError("所有預算必須大於零")


@dataclass
class Investigation:
    backend: Backend
    limits: Limits
    started: float = field(default_factory=time.monotonic)
    calls: int = 0
    evidence: list[Json] = field(default_factory=list)
    trace_ids: set[str] = field(default_factory=set)
    events: list[Json] = field(default_factory=list)
    on_event: Callable[[Json], None] | None = None

    def budget(self) -> Json:
        return {
            "calls_used": self.calls,
            "calls_left": max(0, self.limits.max_tool_calls - self.calls),
            "secs_left": max(0, int(self.limits.max_secs - (time.monotonic() - self.started))),
        }

    def emit(self, event_type: str, **payload: Any) -> None:
        event = {"type": event_type, "payload": payload}
        self.events.append(event)
        if self.on_event:
            self.on_event(event)

    @staticmethod
    def call_id() -> str:
        """Return an opaque identifier shared by one tool call's events."""
        return "call-" + uuid.uuid4().hex


@dataclass
class LoopResult:
    status: Literal["report_ready", "unresolved", "budget_exhausted"]
    reason: str
    report: Json | None
    evidence: list[Json]
    events: list[Json]
    usage: Json
    messages: list[ModelMessage]


class ProcedureIncomplete(Exception):
    pass


def make_tool(definition: Json) -> Tool[Investigation]:
    name = definition["name"]
    validator = Draft202012Validator(definition["parameters"])

    async def call(ctx: RunContext[Investigation], **args: Any) -> Json:
        state = ctx.deps
        if state.calls >= state.limits.max_tool_calls:
            return {"error": "budget_exhausted: return your final response now", "budget": state.budget()}
        state.calls += 1  # Invalid arguments also consume the contract's call budget.
        call_id = state.call_id()
        state.emit("tool.started", call_id=call_id, tool=name, args=args)
        try:
            validator.validate(args)  # Tool.from_schema does not validate these itself.
            if name == "get_trace" and args["trace_id"] not in state.trace_ids:
                raise ValueError("trace_id must come from a successful find_traces call in this investigation")
            observed = await asyncio.wait_for(state.backend(name, args), timeout=10)
            if name in {"get_graph", "list_graph_snapshots"} and len(encode(observed.result).encode()) > TOOL_CAPS[name]:
                raise ValueError("Graph or snapshot index exceeds its tool limit; refusing to drop topology or pagination entries")
            result = bounded_result(observed.result, TOOL_CAPS[name])
            if "error" in result:
                raise ValueError(result["error"])
        except (ValidationError, ValueError, LookupError, OSError, TimeoutError) as error:
            message = safe_error(error)
            state.emit("tool.failed", call_id=call_id, tool=name, args=args, error=message)
            return {"error": message, "call_id": call_id, "budget": state.budget()}
        evidence_id = f"ev-{len(state.evidence) + 1:04d}"
        evidence = {
            "id": evidence_id, "tool": name, "args": args, "source": observed.source,
            "t": observed.t, "summary_zh": observed.summary_zh, "result": result,
        }
        state.evidence.append(evidence)
        if name == "find_traces":
            state.trace_ids.update(row["trace_id"] for row in result.get("traces", []) if "trace_id" in row)
        state.emit("observation.recorded", call_id=call_id, evidence=evidence, evidence_id=evidence_id)
        return {"result": result, "evidence_id": evidence_id, "call_id": call_id, "budget": state.budget()}

    tool = Tool.from_schema(
        call, name=name, description=tool_description(definition),
        json_schema=definition["parameters"], takes_ctx=True, sequential=True,
    )

    async def prepare(ctx: RunContext[Investigation], tool_def: Any) -> Any:
        return tool_def if ctx.deps.calls < ctx.deps.limits.max_tool_calls else None

    tool.prepare = prepare
    return tool


def static_instructions(capabilities: Json, report_schema: Json) -> str:
    nodes = [{"id": node["id"], "kind": node["kind"]} for node in capabilities["nodes"]]
    names = [definition["name"] for definition in capabilities["tools"]]
    graph_mode = "get_graph" in names
    prompt = GRAPH_SYSTEM_PROMPT + GUARDROOM_PROMPT if graph_mode else SYSTEM_PROMPT
    if graph_mode:
        names.append("submit_report")
        report_schema = InvestigationReportBody.model_json_schema()
    return prompt + "available_tools=" + encode(names) + "\nnodes=" + encode(nodes) + "\nreport_schema=" + encode(report_schema)


def validate_report(report: Json, state: Investigation, node_ids: set[str]) -> None:
    histories = [ev for ev in state.evidence if ev["tool"] == "get_node_history" and ev["result"].get("points")]
    traces = [ev for ev in state.evidence if ev["tool"] == "get_trace" and ev["result"].get("path")]
    if not histories or not traces:
        missing = "get_node_history" if not histories else "get_trace"
        raise ProcedureIncomplete(f"procedure_incomplete: no successful nonempty {missing}")
    refs = set(report["cited_evidence_ids"])
    refs.add(report["timeline"]["onset"]["evidence_id"])
    for entry in report["contributing"] + report["ruled_out"]:
        if "evidence_id" in entry:
            refs.add(entry["evidence_id"])
    if refs - {ev["id"] for ev in state.evidence}:
        raise ValueError("Report cites an evidence_id not returned in this investigation")
    nodes = [report["root_cause"], report["timeline"]["onset"]]
    nodes += report["timeline"]["propagation"] + report["contributing"] + report["ruled_out"]
    if any(entry["node"] not in node_ids for entry in nodes):
        raise ValueError("Report references an undeclared node")
    onset = report["timeline"]["onset"]
    if onset["node"] != report["root_cause"]["node"] or onset["t"] > 0:
        raise ValueError("Onset must refer to the root_cause node with t <= 0")
    if not any(
        ev["id"] == onset["evidence_id"] and ev["args"]["node"] == onset["node"]
        and ev["result"]["t_from"] <= onset["t"] <= ev["result"]["t_to"]
        for ev in histories
    ):
        raise ValueError("Onset must cite that node's history covering the claimed time")


async def investigate(
    *, model: Model | str, capabilities: Json, opening: Json, report_schema: Json,
    backend: Backend, limits: Limits | None = None,
    on_event: Callable[[Json], None] | None = None,
    on_context: ContextCallback | None = None,
) -> LoopResult:
    """One incident per invocation. The caller supplies an already sanitized opening."""
    limits = limits or Limits()
    state = Investigation(backend=backend, limits=limits, on_event=on_event)
    report_validator = Draft202012Validator(report_schema)
    node_ids = {node["id"] for node in capabilities["nodes"]}
    definitions = capabilities["tools"]
    names = [definition["name"] for definition in definitions]
    if len(names) != len(set(names)) or set(names) - TOOL_CAPS.keys():
        raise ValueError("工具名稱重複或不是 NightWatch 唯讀工具")
    model_settings = {
        "timeout": limits.request_timeout_secs, "max_tokens": 4096,
        "openai_store": False, "openai_prompt_cache_key": "nightwatch-python-agent-v1",
    }
    instructions = static_instructions(capabilities, report_schema)
    tools_context = [
        {"name": definition["name"], "description": tool_description(definition),
         "parameters": copy.deepcopy(definition["parameters"])}
        for definition in definitions
    ]
    graph_mode = "get_graph" in names
    if graph_mode:
        tools_context.append(submit_definition())
    opening_context = {**copy.deepcopy(opening), "budget": state.budget()}
    model_name = model if isinstance(model, str) else getattr(model, "model_name", type(model).__name__)
    messages: list[ModelMessage] = []
    usage = RunUsage()

    def save_context() -> None:
        if on_context:
            from pydantic_ai.messages import ModelMessagesTypeAdapter
            usage_data = asdict(usage)
            if usage_data["cost"] is not None:
                usage_data["cost"] = str(usage_data["cost"])
            on_context({
                "instructions": instructions, "tools": tools_context, "opening": opening_context,
                "model": {"name": str(model_name), "settings": copy.deepcopy(model_settings)},
                "messages": json.loads(ModelMessagesTypeAdapter.dump_json(messages)),
                "usage": usage_data,
            })

    save_context()
    agent = Agent(
        model, deps_type=Investigation,
        output_type=ToolOutput(InvestigationReportBody, name="submit_report", description=SUBMIT_DESCRIPTION)
        if graph_mode else str,
        instructions=instructions,
        tools=[make_tool(definition) for definition in definitions], retries=1,
        model_settings=model_settings,
    )

    @agent.output_validator
    def check_output(ctx: RunContext[Investigation], output: Any) -> Any:
        if graph_mode:
            call_id = ctx.tool_call_id or state.call_id()
            state.emit("tool.started", call_id=call_id, tool="submit_report", args=output.model_dump())
            try:
                validate_submission(output, state.evidence, node_ids)
            except ValueError as error:
                state.emit("tool.failed", call_id=call_id, tool="submit_report", error=safe_error(error))
                raise ModelRetry(str(error)) from error
            state.emit("report.submitted", call_id=call_id, tool="submit_report", report=output.model_dump())
            return output
        if output.startswith("inconclusive:") and output.removeprefix("inconclusive:").strip():
            return output
        try:
            report = json.loads(output)
            encode(report)  # Reject Python's otherwise accepted NaN/Infinity JSON extensions.
            report_validator.validate(report)
            validate_report(report, ctx.deps, node_ids)
        except (ValueError, ValidationError) as error:
            raise ModelRetry("Invalid report format or evidence references; resubmit once: " + safe_error(error)) from error
        return output

    report = None
    try:
        async with asyncio.timeout(limits.max_secs):
            async with agent.iter(
                encode(opening_context), deps=state, usage=usage,
                usage_limits=UsageLimits(request_limit=limits.max_tool_calls + 3, total_tokens_limit=limits.max_tokens),
            ) as agent_run:
                try:
                    async for _node in agent_run:
                        messages = agent_run.all_messages()
                        save_context()
                finally:
                    messages = agent_run.all_messages()
                    save_context()
                result = agent_run.result
        if result is None:
            raise UnexpectedModelBehavior("Agent ended without a final output")
        if graph_mode:
            report = {"schema_version": REPORT_VERSION, **result.output.model_dump()}
            status = "report_ready" if report["conclusion"] == "supported" else "unresolved"
            reason = report["summary_zh"]
        elif result.output.startswith("inconclusive:"):
            status, reason = "unresolved", result.output
        else:
            status, reason, report = "report_ready", "報告已通過格式、程序與引用檢查；等待後續稽核。", json.loads(result.output)
    except (UsageLimitExceeded, TimeoutError) as error:
        status, reason = "budget_exhausted", safe_error(error)
    except (ProcedureIncomplete, ModelAPIError, APIError, UnexpectedModelBehavior) as error:
        status, reason = "unresolved", safe_error(error)
    state.emit("investigation.finished", status=status, reason=reason)
    usage_data = asdict(usage)
    if usage_data["cost"] is not None:
        usage_data["cost"] = str(usage_data["cost"])
    return LoopResult(status, reason, report, state.evidence, state.events, usage_data, messages)
