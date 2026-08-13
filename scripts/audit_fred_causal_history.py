from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from trading_bot.crisis_radar.catalog import (  # noqa: E402
    FRED_GLOBAL_V2_INDICATORS,
    FRED_HISTORICAL_BACKFILL_MODES,
    FRED_INDICATORS,
    FRED_V11_DEPTH_INDICATORS,
    FRED_V12_RESEARCH_INDICATORS,
)
from trading_bot.crisis_radar.sources.base import SeriesRequest  # noqa: E402
from trading_bot.crisis_radar.sources.fred_client import (  # noqa: E402
    FredClient,
    FredClientError,
)


OFFICIAL_OBSERVATIONS_DOC = (
    "https://fred.stlouisfed.org/docs/api/fred/series_observations.html"
)
OFFICIAL_VINTAGES_DOC = (
    "https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html"
)


@dataclass(frozen=True)
class CapabilityResult:
    indicator_code: str
    provider_series_id: str
    collection_role: str
    declared_backfill_mode: str
    vintage_count_in_window: int | None
    initial_release_probe_rows: int
    capability: str
    contract_status: str
    vintage_error: str | None = None
    probe_error: str | None = None


def configured_series() -> tuple[tuple[object, str], ...]:
    active = tuple(
        (item, "active_scoring")
        for item in FRED_INDICATORS
        + FRED_GLOBAL_V2_INDICATORS
        + FRED_V11_DEPTH_INDICATORS
    )
    research = tuple((item, "disabled_research") for item in FRED_V12_RESEARCH_INDICATORS)
    unique: dict[str, tuple[object, str]] = {}
    for item, role in active + research:
        unique.setdefault(item.code, (item, role))
    return tuple(unique.values())


def _declared_mode(item: object) -> str:
    return str(
        FRED_HISTORICAL_BACKFILL_MODES.get(
            item.code,
            getattr(item, "historical_backfill_mode", "initial_release"),
        )
    )


async def audit_one(
    client: FredClient,
    item: object,
    role: str,
    *,
    started_on: date,
    ended_on: date,
) -> CapabilityResult:
    mode = _declared_mode(item)
    vintage_count: int | None = None
    rows = 0
    vintage_error: str | None = None
    probe_error: str | None = None
    try:
        vintage_payload = json.loads(
            await client.fetch_vintage_dates(
                item.provider_series_id,
                realtime_start=started_on,
                realtime_end=ended_on,
            )
        )
        vintage_dates = vintage_payload.get("vintage_dates", [])
        if not isinstance(vintage_dates, list):
            raise FredClientError("FRED vintage dates returned malformed JSON")
        vintage_count = len(vintage_dates)
    except (FredClientError, json.JSONDecodeError, TypeError, ValueError) as exc:
        vintage_error = f"{type(exc).__name__}:{exc}"

    try:
        probe_payload = json.loads(
            await client.fetch_initial_release_probe(
                SeriesRequest(item.code, item.provider_series_id, item.unit),
                observation_start=started_on,
                observation_end=ended_on,
            )
        )
        observations = probe_payload.get("observations", [])
        if not isinstance(observations, list):
            raise FredClientError("FRED initial-release probe returned malformed JSON")
        rows = sum(
            1
            for observation in observations
            if isinstance(observation, dict)
            and observation.get("value") not in {None, "."}
        )
    except (FredClientError, json.JSONDecodeError, TypeError, ValueError) as exc:
        probe_error = f"{type(exc).__name__}:{exc}"

    capability = (
        "verified_initial_release"
        if rows > 0
        else "no_initial_release_rows"
        if probe_error is None
        else "provider_rejected_probe"
    )
    if mode == "live_only":
        # Some licensed market series are available for current monitoring but
        # explicitly reject ALFRED real-time/vintage requests.  Preserve that
        # limitation as its own state instead of calling it a transient error
        # or pretending that causal history exists.
        contract_status = "live_only"
    elif probe_error is not None:
        # A timeout, rate limit or provider rejection does not prove that
        # history is absent.  It is unknown and still fails closed.
        contract_status = "unknown"
    elif mode == "initial_release":
        contract_status = "verified" if rows > 0 else "mismatch"
    else:
        contract_status = "promotion_candidate" if rows > 0 else "research_only"
    return CapabilityResult(
        indicator_code=item.code,
        provider_series_id=item.provider_series_id,
        collection_role=role,
        declared_backfill_mode=mode,
        vintage_count_in_window=vintage_count,
        initial_release_probe_rows=rows,
        capability=capability,
        contract_status=contract_status,
        vintage_error=vintage_error,
        probe_error=probe_error,
    )


async def build_report(
    client: FredClient,
    *,
    started_on: date,
    ended_on: date,
    selected_codes: set[str] | None = None,
    concurrency: int = 4,
) -> dict[str, object]:
    if ended_on < started_on:
        raise ValueError("audit end date must not precede start date")
    if (ended_on - started_on).days > 1460:
        raise ValueError("audit window must not exceed four years")
    if concurrency < 1 or concurrency > 8:
        raise ValueError("audit concurrency must be between 1 and 8")
    configured = configured_series()
    known_codes = {item.code for item, _role in configured}
    if selected_codes is not None and (
        not selected_codes or not selected_codes <= known_codes
    ):
        raise ValueError("selected_codes must contain configured FRED indicator codes")
    selected = tuple(
        (item, role)
        for item, role in configured
        if selected_codes is None or item.code in selected_codes
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(item: object, role: str) -> CapabilityResult:
        async with semaphore:
            return await audit_one(
                client,
                item,
                role,
                started_on=started_on,
                ended_on=ended_on,
            )

    results = list(await asyncio.gather(*(bounded(item, role) for item, role in selected)))
    counts: dict[str, int] = {}
    for result in results:
        counts[result.contract_status] = counts.get(result.contract_status, 0) + 1
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {"start": started_on.isoformat(), "end": ended_on.isoformat()},
        "official_contracts": {
            "observations": OFFICIAL_OBSERVATIONS_DOC,
            "vintage_dates": OFFICIAL_VINTAGES_DOC,
            "initial_release_output_type": 4,
        },
        "policy": {
            "verified": "may enter causal backfill after full-history validation",
            "mismatch": "must fail closed and cannot enter causal replay",
            "promotion_candidate": "may change only in a new immutable methodology",
            "research_only": "may be collected but must remain excluded from causal replay",
            "live_only": "may score fresh observations but must be excluded from historical replay",
            "unknown": "provider error is not evidence of absence; fail closed and retry",
        },
        "counts": counts,
        "results": [asdict(result) for result in results],
    }


def parse_args() -> argparse.Namespace:
    # FRED validates real-time bounds against its UTC calendar.  A host east of
    # UTC can already be on the next local day while the provider still rejects
    # that date as future data.
    today = datetime.now(timezone.utc).date()
    parser = argparse.ArgumentParser(
        description="Audit causal initial-release history for configured FRED series"
    )
    parser.add_argument("--end", type=date.fromisoformat, default=today)
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=today - timedelta(days=1460),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--indicator",
        action="append",
        dest="indicators",
        help="audit one configured indicator code; repeat to select several",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when an active initial-release contract is not verified",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    load_dotenv()
    client = FredClient(os.environ["FRED_API_KEY"])
    report = await build_report(
        client,
        started_on=args.start,
        ended_on=args.end,
        selected_codes=set(args.indicators) if args.indicators else None,
        concurrency=args.concurrency,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    failures = sum(
        int(report["counts"].get(status, 0))  # type: ignore[union-attr]
        for status in ("mismatch", "unknown")
    )
    return 1 if args.strict and failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
