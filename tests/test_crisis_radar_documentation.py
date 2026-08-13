import json
import hashlib
from pathlib import Path

from trading_bot.crisis_radar.catalog import (
    METHODOLOGY_V11_VERSION,
    METHODOLOGY_V12_VERSION,
    METHODOLOGY_V13_VERSION,
)
from trading_bot.crisis_radar.methodology_contract import (
    runtime_methodology_contract,
)
from trading_bot.crisis_radar.replay_v2 import (
    REPLAY_V2_ENGINE_VERSION,
    REPLAY_V12_ENGINE_VERSION,
    REPLAY_V13_ENGINE_VERSION,
)
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
CAUSAL_DEPTH_EVIDENCE_PATH = (
    ROOT / "docs" / "evidence" / "crisis-radar-fred-causal-backfill-20260813.json"
)
CAUSAL_CAPABILITY_EVIDENCE_PATH = (
    ROOT / "docs" / "evidence" / "crisis-radar-fred-causal-capability-20260812.json"
)
V12_REPLAY_EVIDENCE_PATH = (
    ROOT / "docs" / "evidence" / "crisis-radar-v12-financial-stress-replay-20260813.json"
)
V12_REPLAY_INPUT_EVIDENCE_PATH = (
    ROOT / "docs" / "evidence" / "crisis-radar-v12-replay-input-preparation-20260813.json"
)
V13_REPLAY_EVIDENCE_PATH = (
    ROOT / "docs" / "evidence" / "crisis-radar-v13-financial-stress-replay-20260813.json"
)
V13_REPLAY_SUMMARY_PATH = (
    ROOT
    / "docs"
    / "evidence"
    / "crisis-radar-v13-replay-input-and-sensitivity-20260813.json"
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


def test_new_depth_history_is_causal_but_remains_disabled_and_unpromoted() -> None:
    evidence = json.loads(CAUSAL_DEPTH_EVIDENCE_PATH.read_text(encoding="utf-8"))
    capability = json.loads(CAUSAL_CAPABILITY_EVIDENCE_PATH.read_text(encoding="utf-8"))

    final = evidence["effective_final_state"]
    assert final["series"] == 10
    assert final["causal_points"] == sum(item["points"] for item in evidence["series"])
    assert final["retrospective_revised_points"] == 0
    assert final["release_time_estimated_points"] == 0
    assert final["impossible_release_points"] == 0
    assert final["database_integrity"] == "ok"
    assert final["foreign_key_violations"] == 0
    assert final["working_database_touched"] is False
    assert final["production_database_touched"] is False
    assert evidence["causal_safeguards"]["research_indicators_enabled"] is False
    assert evidence["causal_safeguards"]["v11_thresholds_created"] is False
    assert evidence["causal_safeguards"]["v11_checksum_changed"] is False
    assert "new_immutable_methodology_version" in evidence["promotion_required"]
    assert capability["counts"] == {"verified": 38, "live_only": 1}
    live_only = [
        item["indicator_code"]
        for item in capability["results"]
        if item["contract_status"] == "live_only"
    ]
    assert live_only == ["sp500_30d_drawdown"]


def test_v12_replay_evidence_is_fail_closed_and_cannot_claim_promotion() -> None:
    evidence = json.loads(V12_REPLAY_EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert evidence["methodology"] == METHODOLOGY_V12_VERSION
    assert evidence["manifest_version"] == "crisis-radar-v12-comparison-v1"
    assert evidence["candidate_status"] == "shadow"
    assert evidence["live_probability"] is None
    assert evidence["promotion_gate"]["passed"] is False
    diagnostics = evidence["candidate_replay_diagnostics"]
    assert diagnostics["cutoff_count"] == 220
    assert diagnostics["eligible_cutoff_count"] == 0
    assert diagnostics["stage_counts"] == {"insufficient_data": 220}
    assert diagnostics["numeric_coverage_max"] == "0.3780"
    assert diagnostics["eligibility_reason_counts"] == {
        "insufficient_numeric_coverage": 220
    }
    assert len(evidence["checksums"]["v12_replay"]) == 64
    assert REPLAY_V12_ENGINE_VERSION == "causal-v12-replay-v1"
    expected_checksum = evidence.pop("manifest_checksum")
    canonical = json.dumps(
        evidence,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    assert hashlib.sha256(canonical).hexdigest() == expected_checksum


def test_v12_replay_input_preparation_is_isolated_and_does_not_weaken_gate() -> None:
    evidence = json.loads(V12_REPLAY_INPUT_EVIDENCE_PATH.read_text(encoding="utf-8"))
    replay = json.loads(V12_REPLAY_EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert evidence["official_fred_initial_release_backfill"]["bounded_final_retry"][
        "status"
    ] == "succeeded"
    assert evidence["result"]["manifest_checksum"] == replay["manifest_checksum"]
    assert evidence["result"]["eligible_cutoff_count"] == 0
    assert evidence["result"]["numeric_coverage_max"] == "0.3780"
    assert evidence["result"]["probability"] is None
    assert evidence["safety"] == {
        "working_database_touched": False,
        "production_database_touched": False,
        "candidate_v12_live_indicators_enabled": False,
        "coverage_denominator_reduced_to_force_eligibility": False,
        "current_revisions_relabelled_as_historical_initial_releases": False,
        "disposable_database_distributed": False,
    }


def test_v13_replay_evidence_uses_scenario_coverage_and_stays_unpromoted() -> None:
    evidence = json.loads(V13_REPLAY_EVIDENCE_PATH.read_text(encoding="utf-8"))

    assert evidence["methodology"] == METHODOLOGY_V13_VERSION
    assert evidence["manifest_version"] == "crisis-radar-v13-comparison-v1"
    assert evidence["candidate_status"] == "shadow"
    assert evidence["live_probability"] is None
    assert evidence["promotion_gate"]["passed"] is False
    diagnostics = evidence["candidate_replay_diagnostics"]
    assert diagnostics["coverage_contract"] == "scenario-replay-coverage-v1"
    assert diagnostics["cutoff_count"] == 220
    assert diagnostics["eligible_cutoff_count"] == 29
    assert diagnostics["numeric_coverage_max"] == "0.8333"
    assert diagnostics["global_numeric_coverage_max"] == "0.4024"
    assert diagnostics["stage_counts"] == {
        "insufficient_data": 191,
        "tension": 24,
        "warning": 5,
    }
    assert evidence["results"]["full"]["sample_count"] == 20
    assert evidence["results"]["full"]["positive_count"] == 3
    assert evidence["results"]["full"]["scored_count"] == 0
    assert len(evidence["checksums"]["v13_replay"]) == 64
    assert REPLAY_V13_ENGINE_VERSION == "causal-v13-scenario-replay-v1"
    expected_checksum = evidence.pop("manifest_checksum")
    canonical = json.dumps(
        evidence,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    assert hashlib.sha256(canonical).hexdigest() == expected_checksum


def test_v13_input_and_sensitivity_evidence_keeps_all_safety_gates_closed() -> None:
    summary = json.loads(V13_REPLAY_SUMMARY_PATH.read_text(encoding="utf-8"))
    replay_bytes = V13_REPLAY_EVIDENCE_PATH.read_bytes()
    replay = json.loads(replay_bytes)

    assert summary["methodology"] == {
        "version": METHODOLOGY_V13_VERSION,
        "checksum": "a6108cc6cd26d6dbb02e1dfa927cc2a9f1ab183eb741b939d1659d53f2e9e611",
        "engine": REPLAY_V13_ENGINE_VERSION,
        "coverage_contract": "scenario-replay-coverage-v1",
        "coverage_unit": "scenario_group_max_freshness",
        "minimum_coverage": "0.70",
        "healthy_coverage": "0.85",
        "required_region_minimums": {"us": 1, "other_advanced": 2, "emerging": 2},
        "live_enabled": False,
        "promotion_status": "candidate",
    }
    assert summary["primary_replay"]["file_sha256"] == hashlib.sha256(
        replay_bytes
    ).hexdigest()
    assert summary["primary_replay"]["manifest_checksum"] == replay["manifest_checksum"]
    assert summary["primary_replay"]["eligible_cutoff_count"] == 29
    assert summary["primary_replay"]["resolved_sample_count"] == 20
    assert summary["primary_replay"]["positive_event_count"] == 3
    assert summary["primary_replay"]["scored_count"] == 0
    assert summary["primary_replay"]["probability"] is None
    assert summary["primary_replay"]["promotion_passed"] is False
    assert summary["point_in_time_provenance_fix"][
        "reverse_revision_links_after_rebuild"
    ] == 0
    assert all(item["promotion_passed"] is False for item in summary["sensitivity"])
    assert all(float(item["minimum_coverage"]) >= 0.70 for item in summary["sensitivity"])
    assert summary["safety"] == {
        "working_database_touched": False,
        "production_database_touched": False,
        "candidate_v13_entered_live_service_bootstrap": False,
        "minimum_coverage_lowered_below_070": False,
        "missing_required_channels_allowed": False,
        "single_non_us_region_considered_sufficient": False,
        "global_coverage_hidden": False,
        "current_revisions_relabelled_as_initial_releases": False,
        "probability_fabricated": False,
        "disposable_database_distributed": False,
    }
