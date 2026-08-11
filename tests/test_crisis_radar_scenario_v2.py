from datetime import datetime, timezone
from decimal import Decimal

from trading_bot.crisis_radar.catalog import METHODOLOGY_V11_VERSION, V11_SCENARIOS
from trading_bot.crisis_radar.domain import IndicatorBand
from trading_bot.crisis_radar.exposure import build_exposure_overlay
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.scenario_v2 import calculate_scenario_v2, playbook_for
from trading_bot.crisis_radar.stage_v2 import GroupScoreV2
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.db import Database


def _group(code: str, cluster: str, score: str) -> GroupScoreV2:
    value = Decimal(score)
    band = (
        IndicatorBand.CRITICAL if value >= Decimal(".75")
        else IndicatorBand.DANGER if value >= Decimal(".50")
        else IndicatorBand.WARNING if value >= Decimal(".25")
        else IndicatorBand.NORMAL
    )
    return GroupScoreV2(
        group_code=code,
        cluster_code=cluster,
        score=value,
        band=band,
        subchannel_count=2,
        active_subchannel_count=int(value >= Decimal(".25")),
        thin_group=False,
        contributors=(f"{code}_indicator",),
    )


def test_banking_playbook_requires_independent_anchor_and_explains_chain() -> None:
    definition = next(item for item in V11_SCENARIOS if item.code == "banking_crisis")
    state = calculate_scenario_v2(
        definition,
        (
            _group("banking_stress", "dollar_liquidity_banks", ".85"),
            _group("credit", "corporate_credit", ".75"),
            _group("market_stress", "markets_fx", ".55"),
        ),
    )

    assert state.status == "confirmed"
    assert state.active_independent_clusters == 3
    assert state.missing_anchors != definition.anchor_groups
    assert playbook_for("banking_crisis").causal_chain_ru[0].startswith("Депозиты")


def test_scenario_recovery_is_not_collapsed_to_inactive() -> None:
    definition = next(item for item in V11_SCENARIOS if item.code == "global_recession")
    state = calculate_scenario_v2(
        definition,
        (
            _group("labor", "labor", ".10"),
            _group("credit", "corporate_credit", ".10"),
            _group("real_economy", "real_economy", ".10"),
        ),
        previous_status="confirmed",
        previous_strength=Decimal("70"),
    )

    assert state.status == "recovery_confirmed"
    assert len(state.recovery_confirmations) >= 2


def test_exposure_overlay_is_read_only_and_flags_leveraged_conflict() -> None:
    overlay = build_exposure_overlay(
        ({"id": 1, "symbol": "BTCUSDT", "side": "long", "leverage": 12},),
        ({
            "code": "crypto_leverage_unwind",
            "status": "confirmed",
            "vulnerable_assets": ("BTC", "ALTCOINS"),
            "possible_beneficiaries": ("CASH",),
        },),
    )

    assert overlay["read_only"] is True
    assert overlay["trade_mutations"] is False
    assert overlay["items"][0]["assessment"] == "conflict"
    assert overlay["items"][0]["leverage_vulnerability"] == "high"


def test_live_scorecard_resolves_only_against_versioned_event_catalog(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "scorecard.sqlite3"))
    CrisisRadarService(
        repository,
        feature_flags=CrisisRadarFeatureFlags(scoring_v11=True),
    ).bootstrap()
    with repository.db.connect() as connection:
        methodology_id = connection.execute(
            "SELECT id FROM cr_methodology_versions WHERE version=?",
            (METHODOLOGY_V11_VERSION,),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO cr_signal_scorecards(
                methodology_id, scenario_code, signal_key, first_detected_at,
                first_elevated_at, last_seen_at, invalidated_at, outcome_status,
                peak_strength_text, baseline_stage
            ) VALUES (?, 'global_recession', 'resolved-signal',
                      '2019-01-01T00:00:00+00:00', '2019-01-01T00:00:00+00:00',
                      '2019-02-01T00:00:00+00:00', '2019-02-01T00:00:00+00:00',
                      'invalidated', '60', 'warning')
            """,
            (methodology_id,),
        )

    result = repository.resolve_signal_scorecards(
        methodology_version=METHODOLOGY_V11_VERSION,
        as_of=datetime(2021, 1, 1, tzinfo=timezone.utc),
    )
    payload = repository.signal_scorecards_payload(
        methodology_version=METHODOLOGY_V11_VERSION
    )

    assert result["resolved"] == 1
    assert payload["items"][0]["outcome_status"] == "resolved"
    assert payload["items"][0]["reaction"]["lead_days"] == "365.00"
    assert payload["items"][0]["reaction"]["mfe"] is None
