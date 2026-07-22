from __future__ import annotations

import asyncio

from trading_bot.crisis_radar.agent import OllamaAgentClient


async def main() -> None:
    client = OllamaAgentClient(timeout_seconds=90)
    print(await client.status())
    reply = await client.ask(
        question="Кратко объясни: подтвержден ли кризис?",
        locale="ru",
        mode="fast",
        market_context={
            "as_of": "2026-07-21T00:00:00+00:00",
            "stage": "tension",
            "breadth": {"active": 2, "danger_or_worse": 0},
            "scenarios": [
                {"code": "global_recession", "status": "watch", "confidence": "medium"}
            ],
            "EVIDENCE_CATALOG": ["scenario:global_recession"],
        },
        evidence_codes={"scenario:global_recession"},
        history=[],
    )
    print(
        {
            "answer": reply.answer,
            "evidence": reply.evidence_codes,
            "limitations": reply.limitations,
            "model": reply.model,
            "latency_ms": reply.latency_ms,
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
