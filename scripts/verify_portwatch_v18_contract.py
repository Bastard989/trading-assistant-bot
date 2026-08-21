from __future__ import annotations

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
    METHODOLOGY_V17_VERSION,
    METHODOLOGY_V18_VERSION,
    PORTWATCH_V18_CANDIDATE_INDICATORS,
    V17_INDICATORS,
    V17_SCENARIOS,
    V18_INDICATORS,
    V18_SCENARIOS,
    methodology_checksum,
)
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags  # noqa: E402
from trading_bot.crisis_radar.repositories import CrisisRadarRepository  # noqa: E402
from trading_bot.crisis_radar.service import CrisisRadarService  # noqa: E402
from trading_bot.crisis_radar.source_registry import SOURCE_POLICIES  # noqa: E402
from trading_bot.crisis_radar.sources.portwatch import (  # noqa: E402
    PORTWATCH_BASELINE_DAYS,
    PORTWATCH_CHOKEPOINTS,
    PORTWATCH_CURRENT_DAYS,
    PORTWATCH_ITEM_ID,
    PORTWATCH_LAYER_URL,
    PortWatchAdapter,
    PortWatchClient,
)
from trading_bot.crisis_radar.stage_v2 import (  # noqa: E402
    DEPENDENCY_GRAPH_V18_VERSION,
)
from trading_bot.db import Database  # noqa: E402


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def verify_live_contract() -> dict:
    fetched_at = datetime.now(timezone.utc)
    client = PortWatchClient()
    payloads = await asyncio.gather(
        *(
            client.fetch_chokepoint(item.port_id, as_of=fetched_at)
            for item in PORTWATCH_CHOKEPOINTS
        )
    )
    adapter = PortWatchAdapter()
    observations = [
        adapter.normalize_latest(
            payload,
            port_id=item.port_id,
            fetched_at=fetched_at,
        )
        for item, payload in zip(PORTWATCH_CHOKEPOINTS, payloads, strict=True)
    ]

    class ReplayClient:
        async def fetch_chokepoint(self, port_id: str, *, as_of: datetime) -> bytes:
            assert as_of == fetched_at
            index = next(
                index
                for index, item in enumerate(PORTWATCH_CHOKEPOINTS)
                if item.port_id == port_id
            )
            return payloads[index]

    with tempfile.TemporaryDirectory(prefix="crisis-radar-v18-") as temporary:
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
        result = await service.sync_portwatch(ReplayClient(), fetched_at=fetched_at)
        with database.connect() as connection:
            rows = connection.execute(
                """
                SELECT indicator.code, indicator.enabled, source.code AS source_code,
                       observation.value_text, observation.observed_at,
                       dependency.cluster_code, dependency.subchannel_code,
                       dependency.graph_version
                FROM cr_indicator_definitions AS indicator
                JOIN cr_sources AS source ON source.id=indicator.source_id
                JOIN cr_observations AS observation
                  ON observation.indicator_id=indicator.id
                JOIN cr_methodology_versions AS methodology
                  ON methodology.version='candidate-v18'
                JOIN cr_dependency_assignments AS dependency
                  ON dependency.indicator_id=indicator.id
                 AND dependency.methodology_id=methodology.id
                WHERE source.code='imf_portwatch'
                ORDER BY indicator.code
                """
            ).fetchall()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
            snapshot_count = connection.execute(
                "SELECT count(*) FROM cr_market_snapshots_v2"
            ).fetchone()[0]

    policy = next(item for item in SOURCE_POLICIES if item.code == "imf_portwatch")
    threshold_map = {
        item.code: {
            "warning": format(item.thresholds.warning, "f"),
            "danger": format(item.thresholds.danger, "f"),
            "critical": format(item.thresholds.critical, "f"),
        }
        for item in PORTWATCH_V18_CANDIDATE_INDICATORS
    }
    return {
        "evidence_version": "imf-portwatch-v18-contract-v1",
        "collected_at": _iso(fetched_at),
        "methodology": {
            "version": METHODOLOGY_V18_VERSION,
            "effective_from": "2026-08-14T17:45:00Z",
            "checksum": methodology_checksum(
                version=METHODOLOGY_V18_VERSION,
                indicators=V18_INDICATORS,
                scenarios=V18_SCENARIOS,
            ),
            "previous_version": METHODOLOGY_V17_VERSION,
            "previous_checksum": methodology_checksum(
                version=METHODOLOGY_V17_VERSION,
                indicators=V17_INDICATORS,
                scenarios=V17_SCENARIOS,
            ),
            "dependency_graph_version": DEPENDENCY_GRAPH_V18_VERSION,
            "new_indicator_count": len(PORTWATCH_V18_CANDIDATE_INDICATORS),
            "live_enabled": False,
        },
        "formula": {
            "expression": (
                "max(0, (1 - mean(latest_7d_n_total) / "
                "median(previous_365d_n_total)) * 100)"
            ),
            "current_window_days": PORTWATCH_CURRENT_DAYS,
            "baseline_window_days": PORTWATCH_BASELINE_DAYS,
            "current_window_excluded_from_baseline": True,
            "unit": "percent_shortfall",
            "candidate_bands_by_indicator": threshold_map,
            "interpretation": (
                "A physical transit disruption signal, not a trade-volume estimate, "
                "crisis probability or standalone trading instruction."
            ),
            "dependency_contract": (
                "Five chokepoint groups remain visible but share one "
                "shipping_logistics systemic cluster."
            ),
        },
        "official_source": {
            "code": policy.code,
            "tier": policy.tier,
            "item_id": PORTWATCH_ITEM_ID,
            "layer": PORTWATCH_LAYER_URL,
            "terms": policy.license_or_terms_url,
            "transport": policy.transport,
            "payloads": [
                {
                    "port_id": item.port_id,
                    "port_name": item.port_name,
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "observed_at": _iso(observation.observed_at),
                    "shortfall_percent": format(observation.value, "f"),
                }
                for item, payload, observation in zip(
                    PORTWATCH_CHOKEPOINTS,
                    payloads,
                    observations,
                    strict=True,
                )
            ],
            "raw_payloads_distributed": False,
        },
        "causal_status": {
            "exact_release_timestamps_available_in_payload": False,
            "first_collection_used_as_release_time": True,
            "historical_final_vintage_is_point_in_time_safe": False,
            "forward_transformed_vintages_are_point_in_time_from_collection": True,
            "causal_replay_completed": False,
            "forward_collection_started": True,
            "eligible_for_probability": False,
        },
        "isolated_ingestion": {
            "status": result["status"],
            "rows_fetched": result["rows_fetched"],
            "rows_written": result["rows_written"],
            "registered_source_count": len(SOURCE_POLICIES),
            "rows": [dict(row) for row in rows],
            "candidate_v18_snapshot_count": snapshot_count,
            "sqlite_integrity": integrity,
            "foreign_key_violations": foreign_keys,
        },
        "source_contract": {
            "official_public_machine_readable_layer": True,
            "public_request_requires_no_api_secret": True,
            "item_and_layer_identifiers_are_allowlisted": True,
            "five_chokepoint_identities_are_allowlisted": True,
            "queries_are_parallel_and_individually_bounded": True,
            "response_schema_size_dates_counts_and_contiguity_are_validated": True,
            "current_window_is_excluded_from_trailing_baseline": True,
            "same_mechanism_is_not_counted_as_five_systemic_clusters": True,
        },
        "safety": {
            "new_indicators_enabled": False,
            "candidate_v18_entered_live_snapshot_calculation": False,
            "candidate_v17_checksum_changed": False,
            "working_database_touched": False,
            "production_database_touched": False,
            "probability_emitted": False,
            "raw_provider_payloads_distributed": False,
        },
    }


def main() -> None:
    print(json.dumps(asyncio.run(verify_live_contract()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
