from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .graph import GraphAPI
from .loop import Investigation, Limits, encode, investigate, safe_error, static_instructions
from .prompts import tool_description
from .replay import Recording
from .report import submit_definition


async def run(args: argparse.Namespace) -> int:
    schema = json.loads(args.report_schema.read_text())
    if not args.fixture and not args.replay_model:
        data = GraphAPI(args.graph_url or os.environ.get("NIGHTWATCH_GRAPH_URL", "http://127.0.0.1:9999/api/graph"), args.report_schema.parent)
        await data.prepare()
        run_mode = "live_model_graph_api"
    else:
        data = Recording(args.fixture or args.report_schema.parent.parent / "fixtures/catalog_pool_leak")
        run_mode = "scripted_model_recorded_tools" if args.replay_model else "live_model_recorded_tools"
    if args.describe_context:
        state = Investigation(data.query, Limits())
        print(json.dumps({
            "instructions": static_instructions(data.capabilities, schema),
            "tools": [
                {"name": definition["name"], "description": tool_description(definition), "parameters": definition["parameters"]}
                for definition in data.capabilities["tools"]
            ] + ([submit_definition()] if isinstance(data, GraphAPI) else []),
            "opening": {**data.opening, "budget": state.budget()},
        }, ensure_ascii=False, indent=2))
        return 0
    if args.replay_model:
        model = data.scripted_model()
    else:
        from openai import AsyncOpenAI
        from pydantic_ai.models.openai import OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        key = os.environ.get("NIGHTWATCH_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError("請設定 NIGHTWATCH_LLM_API_KEY 或 OPENAI_API_KEY")
        endpoint = os.environ.get("NIGHTWATCH_LLM_ENDPOINT", "https://api.openai.com/v1/responses")
        base_url = endpoint.removesuffix("/").removesuffix("/responses")
        client = AsyncOpenAI(api_key=key, base_url=base_url, max_retries=2, timeout=60)
        model_name = args.model or os.environ.get("NIGHTWATCH_LLM_MODEL", "gpt-6-astra")
        model = OpenAIResponsesModel(model_name, provider=OpenAIProvider(openai_client=client))
    try:
        result = await investigate(
            model=model, capabilities=data.capabilities, opening=data.opening,
            report_schema=schema, backend=data.query,
            on_event=lambda event: print(encode(event), file=sys.stderr),
        )
    finally:
        if not args.replay_model:
            await client.close()
    print(encode({
        "mode": run_mode,
        "status": result.status, "reason": result.reason, "report": result.report,
        "evidence_count": len(result.evidence), "usage": result.usage,
    }))
    return 0 if result.status == "report_ready" or (isinstance(data, GraphAPI) and result.report is not None) else 1


def main() -> None:
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    parser = argparse.ArgumentParser(description="NightWatch read-only agent: recorded observations or a graph API")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--fixture", type=Path, help="Explicitly investigate a recording instead of Guard Room")
    source.add_argument("--graph-url", help="Guard Room graph URL; defaults to NIGHTWATCH_GRAPH_URL or http://127.0.0.1:9999/api/graph")
    parser.add_argument("--report-schema", type=Path, default=Path("../contracts/schemas/agent-report.schema.json"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--replay-model", action="store_true", help="Replay recorded model calls offline; recording source only")
    mode.add_argument("--describe-context", action="store_true", help="Print English instructions, tool definitions and opening context without calling a model")
    mode.add_argument(
        "--model", nargs="?", const="", metavar="MODEL",
        help="Use a real model; defaults to NIGHTWATCH_LLM_MODEL or gpt-6-astra",
    )
    args = parser.parse_args()
    if args.graph_url and args.replay_model:
        parser.error("--replay-model requires a recording; it cannot be combined with --graph-url")
    try:
        code = asyncio.run(run(args))
    except KeyboardInterrupt:
        code = 130
    except Exception as error:
        print(encode({"status": "unresolved", "reason": safe_error(error)}), file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
