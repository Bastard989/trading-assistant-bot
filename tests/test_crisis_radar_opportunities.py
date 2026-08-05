from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_bot.crisis_radar.opportunities import (
    AssetClass,
    MarketQuote,
    MarketStage,
    OpportunityContext,
    OpportunitySide,
    ScenarioSignal,
    generate_opportunities,
)


NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def _scenario(
    code: str = "global_recession",
    *,
    status: str = "confirmed",
    confidence: str = "high",
) -> ScenarioSignal:
    return ScenarioSignal(code, status, confidence, "1-3m", ("credit", "labor"))


def _quote(
    symbol: str = "SPY",
    *,
    asset_class: AssetClass = AssetClass.ETF,
    exposures: frozenset[str] = frozenset({"us_equity"}),
    as_of: datetime = NOW,
    liquidity: str = "0.95",
    quality: str = "0.90",
    risk: str = "0.55",
    option_risk_profile: str = "linear",
    max_loss: str | None = None,
    max_gain: str | None = None,
    history_size: int = 0,
) -> MarketQuote:
    return MarketQuote(
        symbol=symbol,
        asset_class=asset_class,
        price=Decimal("100"),
        as_of=as_of,
        exposures=exposures,
        liquidity_score=Decimal(liquidity),
        data_quality_score=Decimal(quality),
        risk_score=Decimal(risk),
        expected_move_pct=Decimal("12"),
        adverse_move_pct=Decimal("6"),
        option_risk_profile=option_risk_profile,
        max_loss_pct=None if max_loss is None else Decimal(max_loss),
        max_gain_pct=None if max_gain is None else Decimal(max_gain),
        historical_sample_size=history_size,
        historical_q25_pct=Decimal("5") if history_size else None,
        historical_median_pct=Decimal("8") if history_size else None,
        historical_q75_pct=Decimal("12") if history_size else None,
    )


def _context(*quotes: MarketQuote, scenarios: tuple[ScenarioSignal, ...] | None = None) -> OpportunityContext:
    return OpportunityContext(
        as_of=NOW,
        stage=MarketStage.CONFIRMATION,
        data_quality_score=Decimal("0.90"),
        scenarios=scenarios or (_scenario(),),
        quotes=quotes,
    )


def test_ranked_ideas_are_complete_non_executable_and_bounded() -> None:
    ideas = generate_opportunities(
        _context(
            _quote("SPY"),
            _quote(
                "TLT",
                exposures=frozenset({"duration"}),
                liquidity="0.85",
                risk="0.35",
            ),
        )
    )

    assert 1 <= len(ideas) <= 10
    assert [item.rank for item in ideas] == list(range(1, len(ideas) + 1))
    assert [item.score for item in ideas] == sorted(
        (item.score for item in ideas), reverse=True
    )
    assert {item.side for item in ideas} == {OpportunitySide.SHORT, OpportunitySide.LONG}
    for item in ideas:
        assert item.trigger.ru and item.trigger.en
        assert item.invalidation.ru and item.invalidation.en
        assert item.horizon
        assert item.expected_range_pct.maximum > 0
        assert item.loss_range_pct.minimum < 0
        assert item.evidence and item.limitations
        assert item.analysis_only is True
        assert item.execution_allowed is False
        assert item.personalized_advice is False


def test_ranking_is_stable_when_scenarios_and_quotes_are_reordered() -> None:
    recession = _scenario()
    stress = _scenario("financial_stress", status="elevated", confidence="medium")
    spy = _quote("SPY")
    gold = _quote(
        "GC",
        asset_class=AssetClass.FUTURES,
        exposures=frozenset({"gold"}),
        liquidity="0.80",
        risk="0.40",
    )
    forward = generate_opportunities(_context(spy, gold, scenarios=(recession, stress)))
    reversed_input = generate_opportunities(
        _context(gold, spy, scenarios=(stress, recession))
    )

    assert [(item.idea_key, item.score) for item in forward] == [
        (item.idea_key, item.score) for item in reversed_input
    ]


def test_weak_overall_data_forces_wait() -> None:
    context = OpportunityContext(
        as_of=NOW,
        stage=MarketStage.CRISIS,
        data_quality_score=Decimal("0.49"),
        scenarios=(_scenario(),),
        quotes=(_quote(),),
    )

    ideas = generate_opportunities(context)

    assert len(ideas) == 1
    assert ideas[0].side is OpportunitySide.WAIT
    assert "0.50" in ideas[0].evidence[0].en


def test_stale_relevant_quote_forces_wait() -> None:
    stale = _quote(as_of=NOW - timedelta(days=2))

    ideas = generate_opportunities(_context(stale))

    assert ideas[0].side is OpportunitySide.WAIT
    assert "Stale" in ideas[0].evidence[0].en


def test_watch_low_confidence_or_inactive_scenarios_do_not_create_directional_ideas() -> None:
    for status, confidence in (
        ("unknown", "high"),
        ("watch", "high"),
        ("elevated", "low"),
        ("inactive", "high"),
    ):
        ideas = generate_opportunities(
            _context(_quote(), scenarios=(_scenario(status=status, confidence=confidence),))
        )
        assert ideas[0].side is OpportunitySide.WAIT


def test_unlimited_risk_options_are_rejected_with_explicit_limitation() -> None:
    option = _quote(
        "SPY-PUT",
        asset_class=AssetClass.OPTIONS,
        option_risk_profile="unlimited_risk",
    )

    ideas = generate_opportunities(_context(option))

    assert ideas[0].side is OpportunitySide.WAIT
    assert "defined risk" in ideas[0].evidence[0].en


def test_defined_risk_option_uses_bounded_loss_range() -> None:
    option = _quote(
        "SPY-PUT-SPREAD",
        asset_class=AssetClass.OPTIONS,
        option_risk_profile="defined_risk",
        max_loss="4",
        max_gain="10",
    )

    idea = generate_opportunities(_context(option))[0]

    assert idea.side is OpportunitySide.HEDGE
    assert idea.strategy == "defined_risk_put_spread"
    assert idea.loss_range_pct.minimum == Decimal("-4.0000")
    assert idea.loss_range_pct.maximum == Decimal("-2.0000")
    assert any("maximum loss" in item.en for item in idea.limitations)


def test_all_required_asset_classes_are_declared() -> None:
    assert {item.value for item in AssetClass} == {
        "crypto",
        "crypto_futures",
        "stocks",
        "etf",
        "indices",
        "futures",
        "bonds_rates",
        "fx",
        "commodities",
        "options",
    }


def test_maximum_idea_count_is_enforced() -> None:
    quotes = tuple(_quote(f"EQ-{index}") for index in range(15))

    ideas = generate_opportunities(_context(*quotes), max_ideas=3)

    assert len(ideas) == 3
    assert [item.rank for item in ideas] == [1, 2, 3]
    with pytest.raises(ValueError, match="between 1 and 10"):
        generate_opportunities(_context(*quotes), max_ideas=11)


def test_inputs_require_utc_and_valid_decimal_ranges() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _quote(as_of=datetime(2026, 7, 21, 12))
    with pytest.raises(ValueError, match="must use UTC"):
        _quote(as_of=datetime(2026, 7, 21, 15, tzinfo=timezone(timedelta(hours=3))))
    with pytest.raises(ValueError, match="between 0 and 1"):
        _quote(quality="1.01")
    with pytest.raises(ValueError, match="defined-risk options require"):
        _quote(
            asset_class=AssetClass.OPTIONS,
            option_risk_profile="defined_risk",
        )


def test_production_history_gate_rejects_unverified_expected_return() -> None:
    context = OpportunityContext(
        as_of=NOW,
        stage=MarketStage.CONFIRMATION,
        data_quality_score=Decimal("0.90"),
        scenarios=(_scenario(),),
        quotes=(_quote(),),
        require_historical_distribution=True,
    )
    assert generate_opportunities(context)[0].side is OpportunitySide.WAIT

    verified = OpportunityContext(
        as_of=NOW,
        stage=MarketStage.CONFIRMATION,
        data_quality_score=Decimal("0.90"),
        scenarios=(_scenario(),),
        quotes=(_quote(history_size=12),),
        require_historical_distribution=True,
    )
    idea = generate_opportunities(verified)[0]
    assert idea.historical_sample_size == 12
    assert idea.expected_range_pct.minimum == Decimal("5.0000")


def test_future_quote_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be later"):
        _context(_quote(as_of=NOW + timedelta(seconds=1)))
