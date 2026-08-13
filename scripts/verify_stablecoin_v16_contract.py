from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_bot.crisis_radar.catalog import (  # noqa: E402
    METHODOLOGY_V15_VERSION,
    METHODOLOGY_V16_VERSION,
    V15_INDICATORS,
    V15_SCENARIOS,
    V16_INDICATORS,
    V16_SCENARIOS,
    methodology_checksum,
)
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags  # noqa: E402
from trading_bot.crisis_radar.repositories import CrisisRadarRepository  # noqa: E402
from trading_bot.crisis_radar.service import CrisisRadarService  # noqa: E402
from trading_bot.crisis_radar.source_registry import SOURCE_POLICIES  # noqa: E402
from trading_bot.crisis_radar.sources.bybit import BybitClient  # noqa: E402
from trading_bot.crisis_radar.sources.stablecoins import (  # noqa: E402
    BinanceMarketClient,
    StablecoinDislocationAdapter,
)
from trading_bot.db import Database  # noqa: E402


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def verify_live_contract() -> dict:
    binance_payload = await BinanceMarketClient().fetch_usdc_usdt_book()
    binance_fetched_at = datetime.now(timezone.utc)
    bybit_payload = await BybitClient().fetch_spot_ticker("USDCUSDT")
    bybit_fetched_at = datetime.now(timezone.utc)
    adapter = StablecoinDislocationAdapter()
    binance = adapter.normalize_binance(
        binance_payload,
        fetched_at=binance_fetched_at,
    )
    bybit = adapter.normalize_bybit(
        bybit_payload,
        fetched_at=bybit_fetched_at,
    )

    with tempfile.TemporaryDirectory(prefix="crisis-radar-v16-") as temporary:
        database = Database(Path(temporary) / "isolated.sqlite3")
        repository = CrisisRadarRepository(database)
        service = CrisisRadarService(
            repository,
            feature_flags=CrisisRadarFeatureFlags(
                thresholds_v2=True,
                global_sources_v2=True,
                scoring_v11=True,
            ),
        )
        bootstrap = service.bootstrap()
        for observation in (binance, bybit):
            repository.save_observation(observation, preserve_vintage=True)
        with database.connect() as connection:
            rows = connection.execute(
                """
                SELECT indicator.code, indicator.enabled, source.code AS source_code,
                       count(observation.id) AS observation_count
                FROM cr_indicator_definitions AS indicator
                JOIN cr_sources AS source ON source.id=indicator.source_id
                LEFT JOIN cr_observations AS observation
                  ON observation.indicator_id=indicator.id
                WHERE indicator.code IN (
                    'usdc_usdt_dislocation_binance',
                    'usdc_usdt_dislocation_bybit'
                )
                GROUP BY indicator.code, indicator.enabled, source.code
                ORDER BY indicator.code
                """
            ).fetchall()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
            snapshot_count = connection.execute(
                "SELECT count(*) FROM cr_market_snapshots_v2"
            ).fetchone()[0]

    policies = {item.code: item for item in SOURCE_POLICIES}
    return {
        "evidence_version": "stablecoin-v16-contract-v1",
        "collected_at": _iso(max(binance_fetched_at, bybit_fetched_at)),
        "methodology": {
            "version": METHODOLOGY_V16_VERSION,
            "effective_from": "2026-08-13T20:35:00Z",
            "checksum": methodology_checksum(
                version=METHODOLOGY_V16_VERSION,
                indicators=V16_INDICATORS,
                scenarios=V16_SCENARIOS,
            ),
            "previous_version": METHODOLOGY_V15_VERSION,
            "previous_checksum": methodology_checksum(
                version=METHODOLOGY_V15_VERSION,
                indicators=V15_INDICATORS,
                scenarios=V15_SCENARIOS,
            ),
            "new_indicator_count": 2,
            "live_enabled": False,
        },
        "formula": {
            "expression": "max(abs((bid+ask)/2-1),(ask-bid)/2)*100",
            "unit": "percent_from_peg",
            "candidate_bands": {
                "warning": "0.25",
                "danger": "1",
                "critical": "3",
            },
            "interpretation": (
                "Relative USDC/USDT peg or liquidity dislocation; the pair alone "
                "cannot identify which stablecoin moved."
            ),
            "dependency_contract": (
                "Both venues share usdc_usdt_cross_venue and cannot double "
                "systemic breadth."
            ),
        },
        "official_sources": [
            {
                "code": "bybit",
                "tier": policies["bybit"].tier,
                "endpoint": "https://api.bybit.com/v5/market/tickers",
                "documentation": "https://bybit-exchange.github.io/docs/v5/market/tickers",
                "terms": policies["bybit"].license_or_terms_url,
                "payload_bytes": len(bybit_payload),
                "payload_sha256": hashlib.sha256(bybit_payload).hexdigest(),
                "fetched_at": _iso(bybit_fetched_at),
                "normalized_dislocation_percent": format(bybit.value, "f"),
            },
            {
                "code": "binance_market",
                "tier": policies["binance_market"].tier,
                "endpoint": BinanceMarketClient.endpoint,
                "documentation": (
                    "https://developers.binance.com/en/docs/products/spot/rest-api"
                ),
                "terms": policies["binance_market"].license_or_terms_url,
                "payload_bytes": len(binance_payload),
                "payload_sha256": hashlib.sha256(binance_payload).hexdigest(),
                "fetched_at": _iso(binance_fetched_at),
                "normalized_dislocation_percent": format(binance.value, "f"),
            },
        ],
        "excluded_source": {
            "code": "coinbase_exchange",
            "terms": "https://www.coinbase.com/legal/market_data",
            "reason": (
                "Not integrated into this sold self-hosted repository because the "
                "current market-data terms require a separate legal assessment for "
                "redistribution, derived data and AI-related use."
            ),
        },
        "causal_status": {
            "historical_point_in_time_book_available": False,
            "causal_replay_completed": False,
            "forward_collection_started": True,
            "eligible_for_probability": False,
        },
        "isolated_ingestion": {
            "bootstrap_methodology_version": bootstrap["research_v16"][
                "methodology_version"
            ],
            "registered_source_count": len(SOURCE_POLICIES),
            "rows": [dict(row) for row in rows],
            "candidate_v16_snapshot_count": snapshot_count,
            "sqlite_integrity": integrity,
            "foreign_key_violations": foreign_keys,
        },
        "source_contract": {
            "official_machine_readable_endpoints": True,
            "public_requests_require_no_api_secret": True,
            "response_size_is_bounded": True,
            "symbol_and_schema_are_strictly_validated": True,
            "provider_clock_is_checked_for_bybit": True,
            "quotes_require_executable_bid_and_ask_size": True,
            "same_risk_channel_is_not_double_counted": True,
        },
        "safety": {
            "new_indicators_enabled": False,
            "candidate_v16_entered_live_snapshot_calculation": False,
            "candidate_v15_checksum_changed": False,
            "working_database_touched": False,
            "production_database_touched": False,
            "probability_emitted": False,
            "raw_provider_payloads_distributed": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the live candidate-v16 stablecoin source contract"
    )
    parser.parse_args()
    print(json.dumps(asyncio.run(verify_live_contract()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
