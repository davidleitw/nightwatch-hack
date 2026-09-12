from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .loop import encode, investigate, safe_error
from .replay import Recording


async def run(args: argparse.Namespace) -> int:
    recording = Recording(args.fixture)
    schema = json.loads(args.report_schema.read_text())
    if args.replay_model:
        model = recording.scripted_model()
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
        model_name = args.model or os.environ.get("NIGHTWATCH_LLM_MODEL", "gpt-5.6-luna")
        model = OpenAIResponsesModel(model_name, provider=OpenAIProvider(openai_client=client))
    try:
        result = await investigate(
            model=model, capabilities=recording.capabilities, opening=recording.opening,
            report_schema=schema, backend=recording.query,
            on_event=lambda event: print(encode(event), file=sys.stderr),
        )
    finally:
        if not args.replay_model:
            await client.close()
    print(encode({
        "mode": "scripted_model_recorded_tools" if args.replay_model else "live_model_recorded_tools",
        "status": result.status, "reason": result.reason, "report": result.report,
        "evidence_count": len(result.evidence), "usage": result.usage,
    }))
    return 0 if result.status == "report_ready" else 1


def main() -> None:
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    parser = argparse.ArgumentParser(description="NightWatch 最小 agent loop；工具資料使用明確指定的錄影")
    parser.add_argument("--fixture", type=Path, required=True, help="既有事故錄影目錄")
    parser.add_argument("--report-schema", type=Path, default=Path("../contracts/schemas/agent-report.schema.json"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--replay-model", action="store_true", help="離線重播已錄下的模型呼叫；不測試 LLM 推理")
    mode.add_argument(
        "--model", nargs="?", const="", metavar="MODEL",
        help="用真模型查詢錄影；省略名稱時讀 NIGHTWATCH_LLM_MODEL，預設 gpt-5.6-luna",
    )
    args = parser.parse_args()
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
