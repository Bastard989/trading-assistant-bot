import json
from pathlib import Path

from trading_bot.crisis_radar.catalog import METHODOLOGY_V11_VERSION
from trading_bot.crisis_radar.methodology_contract import (
    runtime_methodology_contract,
)
from trading_bot.crisis_radar.replay_v2 import REPLAY_V2_ENGINE_VERSION
from trading_bot.crisis_radar.scoring_v2 import PROFILES, SCORING_VERSION
from trading_bot.crisis_radar.stage_v2 import (
    DEPENDENCY_GRAPH_VERSION,
    STAGE_VERSION,
)


ROOT = Path(__file__).resolve().parents[1]
GUIDE = (ROOT / "docs" / "crisis-radar-guide.md").read_text(encoding="utf-8")
MODEL_CARD = (ROOT / "docs" / "crisis-radar-model-card.md").read_text(encoding="utf-8")
RUNTIME_CONTRACT_PATH = ROOT / "docs" / "crisis-radar-v2-runtime-contract.json"
ROLLOUT_EVIDENCE_PATH = (
    ROOT / "docs" / "evidence" / "crisis-radar-v2-server-rollout-20260811.json"
)
DEPTH_EVIDENCE_PATH = (
    ROOT / "docs" / "evidence" / "crisis-radar-depth-research-20260811.json"
)


def test_documented_runtime_versions_match_code() -> None:
    for version in (
        METHODOLOGY_V11_VERSION,
        SCORING_VERSION,
        STAGE_VERSION,
        DEPENDENCY_GRAPH_VERSION,
        REPLAY_V2_ENGINE_VERSION,
    ):
        assert version in GUIDE or version in MODEL_CARD


def test_documented_profiles_match_runtime_weights_and_history_gates() -> None:
    for profile in PROFILES.values():
        assert profile.code in GUIDE
        assert f"| {profile.code} |" in GUIDE
        assert f"| {profile.minimum_history} |" in GUIDE
        for weight in (
            profile.economic,
            profile.historical,
            profile.trend,
            profile.acceleration,
            profile.persistence,
            profile.regime,
        ):
            assert format(weight, "f").lstrip("0") in GUIDE


def test_machine_readable_methodology_contract_matches_runtime_exactly() -> None:
    documented = json.loads(RUNTIME_CONTRACT_PATH.read_text(encoding="utf-8"))

    assert documented == runtime_methodology_contract()
    assert "crisis-radar-v2-runtime-contract.json" in GUIDE


def test_documentation_does_not_claim_v11_probability_or_primary_promotion() -> None:
    combined = f"{GUIDE}\n{MODEL_CARD}"
    assert "live_probability: null" in combined
    assert "candidate-v11: shadow" in combined
    assert "eligible historical financial-stress samples: 0" in combined
    assert "нулевое число v11-точек" in combined


def test_server_rollout_evidence_is_sanitized_and_keeps_external_gates_open() -> None:
    evidence = json.loads(ROLLOUT_EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert evidence["version"] == 2
    assert evidence["release"]["id"] == "715384d"
    assert evidence["release"]["source_commit"].startswith("715384d")
    assert evidence["release"]["schema_version"] == 23
    assert evidence["release"]["immutable_read_only_for_service"] is True
    assert evidence["initial_schema_migration_backup"]["schema"] == 20
    assert evidence["initial_schema_migration_backup"]["restore_table_counts_match"] is True
    assert evidence["pre_update_backup"]["schema"] == 23
    assert evidence["pre_update_backup"]["restore_table_counts_match"] is True
    assert evidence["migration"]["working_database_changed_during_shadow"] is False
    assert evidence["runtime"]["official_news_contracts_passed"] == 12
    assert evidence["first_v11_snapshot"]["probability"] is None
    assert evidence["canary"]["status"] == "in_progress"
    assert evidence["canary"]["release"] == evidence["release"]["id"]
    assert evidence["canary"]["critical_incident_count"] == 0
    assert evidence["canary"]["incident_deduplication_live_verified"] is True
    assert evidence["ci"]["head_sha"] == evidence["release"]["source_commit"]
    assert evidence["ci"]["conclusion"] == "success"
    assert "fourteen_calendar_day_canary" in evidence["external_blockers"]
    assert evidence["server_doctor"]["age_binary_available"] is True
    assert evidence["server_doctor"]["required_failed"] == [
        "permanent_https",
        "backup_age_recipient",
        "off_host_backup_directory",
    ]
    serialized = json.dumps(evidence, sort_keys=True).lower()
    assert "telegram_bot_token" not in serialized
    assert "fred_api_key" not in serialized


def test_depth_research_evidence_is_historical_and_cannot_claim_v11_promotion() -> None:
    evidence = json.loads(DEPTH_EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert evidence["live_contracts"]["depth_research_passed"] == 10
    assert evidence["live_contracts"]["depth_research_configured"] == 10
    assert evidence["isolated_backfill"]["status"] == "succeeded"
    assert evidence["isolated_backfill"]["errors"] == []
    assert evidence["isolated_backfill"]["rows_written"] == sum(
        item["points"] for item in evidence["series"]
    )
    assert evidence["isolated_backfill"]["causal_initial_release_points"] == sum(
        item["points"] for item in evidence["series"] if item["causal_replay_eligible"]
    )
    assert evidence["isolated_backfill"]["current_revision_research_points"] == sum(
        item["points"] for item in evidence["series"] if not item["causal_replay_eligible"]
    )
    assert evidence["isolated_backfill"]["working_database_touched"] is False
    assert evidence["safety"]["registered_enabled"] is False
    assert evidence["safety"]["v11_thresholds_created"] is False
    assert evidence["safety"]["v11_checksum_changed"] is False
    assert evidence["safety"]["research_failure_degrades_required_fred_health"] is False
    assert evidence["safety"]["current_revision_points_flagged_retrospective_revised"] is True
    assert "new_immutable_methodology_version" in evidence["safety"]["promotion_required"]
