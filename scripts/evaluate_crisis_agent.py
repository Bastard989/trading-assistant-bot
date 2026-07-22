from __future__ import annotations

import argparse
import asyncio
import json
import math
import os

from dotenv import load_dotenv

from trading_bot.crisis_radar.agent import OllamaAgentClient
from trading_bot.crisis_radar.agent_eval import golden_cases, run_golden_case


def percentile(values: list[int], ratio: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(math.ceil(len(ordered) * ratio) - 1, len(ordered) - 1))
    return ordered[index]


async def run(args: argparse.Namespace) -> int:
    client = OllamaAgentClient(
        base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        model=os.getenv("CRISIS_AGENT_MODEL", "qwen3.5:9b"),
        timeout_seconds=args.timeout,
    )
    selected = [case for case in golden_cases() if not args.case or case.code == args.case]
    if not selected:
        raise SystemExit(f"unknown eval case: {args.case}")
    results = []
    errors = []
    stopped_early = False
    for run_number in range(1, args.runs + 1):
        for case in selected:
            try:
                result = await run_golden_case(client, case, mode=args.mode)
            except Exception as exc:
                error = {
                    "run": run_number,
                    "code": case.code,
                    "error": type(exc).__name__,
                    "detail": str(exc)[:200],
                }
                errors.append(error)
                print(json.dumps(error, ensure_ascii=False, sort_keys=True), flush=True)
            else:
                results.append(result)
                payload = {"run": run_number, **result.payload()}
                print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
                if (
                    "model_timeout" in result.grounding_issues
                    and not args.continue_after_timeout
                ):
                    stopped_early = True
                    break
            if args.pause > 0:
                await asyncio.sleep(args.pause)
        if stopped_early:
            break
    passed = sum(result.passed for result in results)
    latencies = [result.latency_ms for result in results]
    planned = len(selected) * args.runs
    summary = {
        "model": client.model,
        "mode": args.mode,
        "passed": passed,
        "attempted": len(results) + len(errors),
        "planned": planned,
        "errors": len(errors),
        "timeouts": sum("model_timeout" in result.grounding_issues for result in results),
        "stopped_early": stopped_early,
        "average_latency_ms": (
            round(sum(latencies) / len(latencies)) if latencies else None
        ),
        "p50_latency_ms": percentile(latencies, 0.50),
        "p95_latency_ms": percentile(latencies, 0.95),
    }
    print(json.dumps({"summary": summary}, ensure_ascii=False, sort_keys=True))
    return 0 if passed == planned and not errors and not stopped_early else 1


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run live RU/EN golden evals for the local Crisis Radar agent")
    parser.add_argument("--case", default="", help="Run one case code")
    parser.add_argument("--mode", choices=("fast", "deep"), default="fast")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--runs", type=int, choices=range(1, 11), default=1)
    parser.add_argument("--pause", type=float, default=0, help="Pause between cases in seconds")
    parser.add_argument(
        "--continue-after-timeout",
        action="store_true",
        help="Keep running after a timed-out model response (unsafe on a memory-constrained host)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
