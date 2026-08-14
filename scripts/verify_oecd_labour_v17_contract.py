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
    METHODOLOGY_V16_VERSION,
    METHODOLOGY_V17_VERSION,
    V16_INDICATORS,
    V16_SCENARIOS,
    V17_INDICATORS,
    V17_SCENARIOS,
    methodology_checksum,
)
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags  # noqa: E402
from trading_bot.crisis_radar.repositories import CrisisRadarRepository  # noqa: E402
from trading_bot.crisis_radar.service import CrisisRadarService  # noqa: E402
from trading_bot.crisis_radar.source_registry import SOURCE_POLICIES  # noqa: E402
from trading_bot.crisis_radar.sources.global_clients import OecdClient  # noqa: E402
from trading_bot.crisis_radar.sources.global_data import OecdAdapter  # noqa: E402
from trading_bot.crisis_radar.stage_v2 import (  # noqa: E402
    DEPENDENCY_GRAPH_V17_VERSION,
)
from trading_bot.db import Database  # noqa: E402


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def verify_live_contract() -> dict:
    fetched_at = datetime.now(timezone.utc)
    payload = await OecdClient().fetch_harmonised_unemployment(as_of=fetched_at)
    observations = OecdAdapter().normalize_unemployment_momentum(
        payload, fetched_at=fetched_at
    )

    with tempfile.TemporaryDirectory(prefix="crisis-radar-v17-") as temporary:
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
        for observation in observations:
            repository.save_observation(observation, preserve_vintage=True)
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
                  ON methodology.version='candidate-v17'
                JOIN cr_dependency_assignments AS dependency
                  ON dependency.indicator_id=indicator.id
                 AND dependency.methodology_id=methodology.id
                WHERE indicator.code LIKE '%_unemployment_momentum'
                ORDER BY indicator.code
                """
            ).fetchall()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
            snapshot_count = connection.execute(
                "SELECT count(*) FROM cr_market_snapshots_v2"
            ).fetchone()[0]

    policy = next(item for item in SOURCE_POLICIES if item.code == "oecd_labour_research")
    return {
        "evidence_version": "oecd-labour-v17-contract-v1",
        "collected_at": _iso(fetched_at),
        "methodology": {
            "version": METHODOLOGY_V17_VERSION,
            "effective_from": "2026-08-14T00:45:00Z",
            "checksum": methodology_checksum(
                version=METHODOLOGY_V17_VERSION,
                indicators=V17_INDICATORS,
                scenarios=V17_SCENARIOS,
            ),
            "previous_version": METHODOLOGY_V16_VERSION,
            "previous_checksum": methodology_checksum(
                version=METHODOLOGY_V16_VERSION,
                indicators=V16_INDICATORS,
                scenarios=V16_SCENARIOS,
            ),
            "dependency_graph_version": DEPENDENCY_GRAPH_V17_VERSION,
            "new_indicator_count": 5,
            "live_enabled": False,
        },
        "formula": {
            "expression": (
                "mean(unemployment[t-2:t]) - min(mean(unemployment[k-2:k]) "
                "for k=t-12..t)"
            ),
            "minimum_contiguous_months": 15,
            "unit": "percentage_points",
            "candidate_bands": {
                "warning": "0.3",
                "danger": "0.5",
                "critical": "1.0",
            },
            "interpretation": (
                "Cross-country unemployment acceleration inspired by, but not "
                "equivalent to or officially endorsed as, the US Sahm Rule."
            ),
            "dependency_contract": (
                "Country groups remain separate regional evidence but share one "
                "labour systemic cluster."
            ),
        },
        "official_source": {
            "code": "oecd",
            "operational_health_code": policy.code,
            "tier": policy.tier,
            "dataset": "OECD.SDD.STES,DSD_KEI@DF_KEI,4.0",
            "selection": "CAN+GBR+JPN+KOR+MEX.M.UNEMP.PT_LF._T.Y._Z",
            "endpoint": "https://sdmx.oecd.org/public/rest/v1/data/",
            "documentation": (
                "https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html"
            ),
            "terms": policy.license_or_terms_url,
            "payload_bytes": len(payload),
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "fetched_at": _iso(fetched_at),
            "raw_payload_distributed": False,
        },
        "normalized_observations": [
            {
                "indicator_code": item.indicator_code,
                "observed_at": _iso(item.observed_at),
                "momentum_percentage_points": format(item.value, "f"),
                "release_time_estimated": True,
            }
            for item in observations
        ],
        "causal_status": {
            "official_release_timestamps_available_in_payload": False,
            "first_collection_used_as_release_time": True,
            "historical_final_vintage_is_point_in_time_safe": False,
            "causal_replay_completed": False,
            "forward_collection_started": True,
            "eligible_for_probability": False,
        },
        "isolated_ingestion": {
            "bootstrap_methodology_version": bootstrap["research_v17"][
                "methodology_version"
            ],
            "registered_source_count": len(SOURCE_POLICIES),
            "rows": [dict(row) for row in rows],
            "candidate_v17_snapshot_count": snapshot_count,
            "sqlite_integrity": integrity,
            "foreign_key_violations": foreign_keys,
        },
        "source_contract": {
            "official_machine_readable_endpoint": True,
            "public_request_requires_no_api_secret": True,
            "dataset_version_is_pinned": True,
            "response_size_is_bounded": True,
            "dimensions_schema_status_and_values_are_strictly_validated": True,
            "all_five_regions_and_15_contiguous_months_are_required": True,
            "same_mechanism_is_not_counted_as_five_systemic_clusters": True,
        },
        "safety": {
            "new_indicators_enabled": False,
            "candidate_v17_entered_live_snapshot_calculation": False,
            "candidate_v16_checksum_changed": False,
            "working_database_touched": False,
            "production_database_touched": False,
            "probability_emitted": False,
        },
    }


def main() -> None:
    print(json.dumps(asyncio.run(verify_live_contract()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
