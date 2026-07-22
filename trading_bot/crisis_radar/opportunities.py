from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN
from enum import Enum
from typing import Iterable


ZERO = Decimal("0")
ONE = Decimal("1")
FOUR_PLACES = Decimal("0.0001")


class AssetClass(str, Enum):
    CRYPTO = "crypto"
    CRYPTO_FUTURES = "crypto_futures"
    STOCKS = "stocks"
    ETF = "etf"
    INDICES = "indices"
    FUTURES = "futures"
    BONDS_RATES = "bonds_rates"
    FX = "fx"
    COMMODITIES = "commodities"
    OPTIONS = "options"


class OpportunitySide(str, Enum):
    LONG = "long"
    SHORT = "short"
    HEDGE = "hedge"
    WAIT = "wait"


class MarketStage(str, Enum):
    STABLE = "stable"
    TENSION = "tension"
    WARNING = "warning"
    CONFIRMATION = "confirmation"
    CRISIS = "crisis"


@dataclass(frozen=True)
class LocalizedText:
    ru: str
    en: str

    def __post_init__(self) -> None:
        if not self.ru.strip() or not self.en.strip():
            raise ValueError("localized text must contain both ru and en")


@dataclass(frozen=True)
class PercentRange:
    minimum: Decimal
    maximum: Decimal

    def __post_init__(self) -> None:
        if not self.minimum.is_finite() or not self.maximum.is_finite():
            raise ValueError("range bounds must be finite")
        if self.minimum > self.maximum:
            raise ValueError("range minimum must not exceed maximum")


def _unit_interval(value: Decimal, field: str) -> None:
    if not value.is_finite() or value < ZERO or value > ONE:
        raise ValueError(f"{field} must be between 0 and 1")


def _positive(value: Decimal, field: str) -> None:
    if not value.is_finite() or value <= ZERO:
        raise ValueError(f"{field} must be finite and positive")


def _utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field} must use UTC")


@dataclass(frozen=True)
class ScenarioSignal:
    code: str
    status: str
    confidence: str
    horizon: str
    evidence_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.horizon.strip():
            raise ValueError("scenario code and horizon must not be empty")
        if self.status not in {"inactive", "watch", "elevated", "confirmed"}:
            raise ValueError("unknown scenario status")
        if self.confidence not in {"low", "medium", "high"}:
            raise ValueError("unknown scenario confidence")


@dataclass(frozen=True)
class MarketQuote:
    symbol: str
    asset_class: AssetClass
    price: Decimal
    as_of: datetime
    exposures: frozenset[str]
    liquidity_score: Decimal
    data_quality_score: Decimal
    risk_score: Decimal
    expected_move_pct: Decimal
    adverse_move_pct: Decimal
    max_age_seconds: int = 86400
    option_risk_profile: str = "linear"
    max_loss_pct: Decimal | None = None
    max_gain_pct: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.exposures:
            raise ValueError("quote symbol and exposures must not be empty")
        _positive(self.price, "price")
        _utc(self.as_of, "quote as_of")
        _unit_interval(self.liquidity_score, "liquidity_score")
        _unit_interval(self.data_quality_score, "data_quality_score")
        _unit_interval(self.risk_score, "risk_score")
        _positive(self.expected_move_pct, "expected_move_pct")
        _positive(self.adverse_move_pct, "adverse_move_pct")
        if self.max_age_seconds < 1:
            raise ValueError("max_age_seconds must be positive")
        if self.option_risk_profile not in {"linear", "defined_risk", "unlimited_risk"}:
            raise ValueError("unknown option risk profile")
        if self.asset_class is AssetClass.OPTIONS:
            if self.option_risk_profile == "linear":
                raise ValueError("options must declare a risk profile")
            if self.option_risk_profile == "defined_risk" and self.max_loss_pct is None:
                raise ValueError("defined-risk options require max_loss_pct")
        if self.max_loss_pct is not None:
            _positive(self.max_loss_pct, "max_loss_pct")
        if self.max_gain_pct is not None:
            _positive(self.max_gain_pct, "max_gain_pct")


@dataclass(frozen=True)
class OpportunityContext:
    as_of: datetime
    stage: MarketStage
    data_quality_score: Decimal
    scenarios: tuple[ScenarioSignal, ...]
    quotes: tuple[MarketQuote, ...]

    def __post_init__(self) -> None:
        _utc(self.as_of, "context as_of")
        _unit_interval(self.data_quality_score, "data_quality_score")
        if any(quote.as_of > self.as_of for quote in self.quotes):
            raise ValueError("quote as_of must not be later than context as_of")


@dataclass(frozen=True)
class OpportunityIdea:
    rank: int
    idea_key: str
    symbol: str
    asset_class: AssetClass
    side: OpportunitySide
    strategy: str
    score: Decimal
    trigger: LocalizedText
    invalidation: LocalizedText
    horizon: str
    expected_range_pct: PercentRange
    loss_range_pct: PercentRange
    rationale: LocalizedText
    evidence: tuple[LocalizedText, ...]
    limitations: tuple[LocalizedText, ...]
    analysis_only: bool = True
    execution_allowed: bool = False
    personalized_advice: bool = False

    def __post_init__(self) -> None:
        if self.rank < 1 or not self.idea_key or not self.symbol or not self.strategy:
            raise ValueError("opportunity identity fields must not be empty")
        _unit_interval(self.score, "score")
        if not self.horizon.strip() or not self.evidence or not self.limitations:
            raise ValueError("opportunity explanation fields must not be empty")
        if not self.analysis_only or self.execution_allowed or self.personalized_advice:
            raise ValueError("opportunities must remain non-executable general analytics")


@dataclass(frozen=True)
class _Template:
    scenario_code: str
    exposure: str
    asset_classes: tuple[AssetClass, ...]
    side: OpportunitySide
    strategy: str
    rationale: LocalizedText


_TEMPLATES = (
    _Template(
        "global_recession",
        "us_equity",
        (AssetClass.STOCKS, AssetClass.ETF, AssetClass.INDICES, AssetClass.FUTURES),
        OpportunitySide.SHORT,
        "directional_short",
        LocalizedText(
            "Слабость глобального роста может давить на циклические и широкие рынки акций.",
            "Weak global growth can pressure cyclical and broad equity markets.",
        ),
    ),
    _Template(
        "global_recession",
        "duration",
        (AssetClass.BONDS_RATES, AssetClass.ETF, AssetClass.FUTURES),
        OpportunitySide.LONG,
        "long_duration",
        LocalizedText(
            "Замедление роста может поддерживать качественную дюрацию при снижении ставок.",
            "Slower growth can support high-quality duration if rates decline.",
        ),
    ),
    _Template(
        "global_recession",
        "us_equity",
        (AssetClass.OPTIONS,),
        OpportunitySide.HEDGE,
        "defined_risk_put_spread",
        LocalizedText(
            "Ограниченный по риску put-spread может хеджировать падение рынка акций.",
            "A defined-risk put spread can hedge an equity-market decline.",
        ),
    ),
    _Template(
        "financial_stress",
        "gold",
        (AssetClass.COMMODITIES, AssetClass.ETF, AssetClass.FUTURES),
        OpportunitySide.HEDGE,
        "liquidity_stress_hedge",
        LocalizedText(
            "Золото может служить хеджем системного стресса, но чувствительно к ликвидности.",
            "Gold can hedge systemic stress, while remaining sensitive to liquidity.",
        ),
    ),
    _Template(
        "financial_stress",
        "us_equity",
        (AssetClass.OPTIONS,),
        OpportunitySide.HEDGE,
        "defined_risk_put_spread",
        LocalizedText(
            "Put-spread с ограниченным убытком может хеджировать расширение финансового стресса.",
            "A limited-loss put spread can hedge an escalation in financial stress.",
        ),
    ),
    _Template(
        "oil_stagflation",
        "oil",
        (AssetClass.COMMODITIES, AssetClass.ETF, AssetClass.FUTURES),
        OpportunitySide.LONG,
        "inflation_shock_long",
        LocalizedText(
            "Нефтяной инфляционный шок поддерживает инструменты с прямой нефтяной экспозицией.",
            "An oil-driven inflation shock supports instruments with direct oil exposure.",
        ),
    ),
    _Template(
        "crypto_leverage_unwind",
        "crypto",
        (AssetClass.CRYPTO_FUTURES,),
        OpportunitySide.SHORT,
        "leverage_unwind_short",
        LocalizedText(
            "Сброс плечей может усиливать нисходящее движение криптовалютных фьючерсов.",
            "Leverage unwinds can amplify downside moves in crypto futures.",
        ),
    ),
    _Template(
        "crypto_leverage_unwind",
        "crypto",
        (AssetClass.OPTIONS,),
        OpportunitySide.HEDGE,
        "defined_risk_put_spread",
        LocalizedText(
            "Опционный put-spread ограничивает стоимость хеджа криптовалютного снижения.",
            "An option put spread caps the cost of hedging crypto downside.",
        ),
    ),
    _Template(
        "china_hard_landing",
        "china_equity",
        (AssetClass.STOCKS, AssetClass.ETF, AssetClass.INDICES, AssetClass.FUTURES),
        OpportunitySide.SHORT,
        "china_growth_short",
        LocalizedText(
            "Резкое замедление Китая может давить на китайские акции и связанные индексы.",
            "A sharp China slowdown can pressure Chinese equities and related indices.",
        ),
    ),
    _Template(
        "china_hard_landing",
        "usd",
        (AssetClass.FX,),
        OpportunitySide.LONG,
        "defensive_fx_long",
        LocalizedText(
            "Защитный спрос при замедлении Китая может поддерживать долларовые валютные пары.",
            "Defensive demand during a China slowdown can support USD currency pairs.",
        ),
    ),
)


_STATUS_SCORE = {
    "inactive": ZERO,
    "watch": Decimal("0.35"),
    "elevated": Decimal("0.70"),
    "confirmed": ONE,
}
_CONFIDENCE_SCORE = {
    "low": Decimal("0.35"),
    "medium": Decimal("0.70"),
    "high": ONE,
}
_STAGE_SCORE = {
    MarketStage.STABLE: Decimal("0.15"),
    MarketStage.TENSION: Decimal("0.35"),
    MarketStage.WARNING: Decimal("0.60"),
    MarketStage.CONFIRMATION: Decimal("0.80"),
    MarketStage.CRISIS: ONE,
}


def _q(value: Decimal) -> Decimal:
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_EVEN)


def _asymmetry(quote: MarketQuote) -> Decimal:
    gain = quote.max_gain_pct or quote.expected_move_pct
    loss = quote.max_loss_pct or quote.adverse_move_pct
    ratio = gain / loss
    return min(ONE, ratio / Decimal("2"))


def _score(context: OpportunityContext, scenario: ScenarioSignal, quote: MarketQuote) -> Decimal:
    confirmation = (
        _STATUS_SCORE[scenario.status] * Decimal("0.75")
        + _STAGE_SCORE[context.stage] * Decimal("0.25")
    )
    quality = min(
        context.data_quality_score,
        quote.data_quality_score,
        _CONFIDENCE_SCORE[scenario.confidence],
    )
    score = (
        confirmation * Decimal("0.30")
        + quote.liquidity_score * Decimal("0.20")
        + _asymmetry(quote) * Decimal("0.20")
        + quality * Decimal("0.20")
        + (ONE - quote.risk_score) * Decimal("0.10")
    )
    return _q(max(ZERO, min(ONE, score)))


def _ranges(quote: MarketQuote) -> tuple[PercentRange, PercentRange]:
    gain = quote.max_gain_pct or quote.expected_move_pct
    loss = quote.max_loss_pct or quote.adverse_move_pct
    return (
        PercentRange(_q(gain * Decimal("0.50")), _q(gain)),
        PercentRange(_q(-loss), _q(-loss * Decimal("0.50"))),
    )


def _is_stale(context: OpportunityContext, quote: MarketQuote) -> bool:
    age = context.as_of - quote.as_of
    return age.total_seconds() > quote.max_age_seconds


def _wait_idea(reasons: Iterable[LocalizedText]) -> OpportunityIdea:
    reason_items = tuple(reasons) or (
        LocalizedText(
            "Нет подтверждённого сочетания сценария и подходящего ликвидного инструмента.",
            "There is no confirmed combination of a scenario and a suitable liquid instrument.",
        ),
    )
    return OpportunityIdea(
        rank=1,
        idea_key="wait:market",
        symbol="MARKET",
        asset_class=AssetClass.INDICES,
        side=OpportunitySide.WAIT,
        strategy="wait_for_confirmation",
        score=ZERO,
        trigger=LocalizedText(
            "Возобновить оценку после улучшения качества данных или подтверждения сценария.",
            "Reassess after data quality improves or the scenario gains confirmation.",
        ),
        invalidation=LocalizedText(
            "Режим ожидания прекращается только после появления свежего проверяемого сигнала.",
            "The wait state ends only when a fresh, verifiable signal becomes available.",
        ),
        horizon="until-confirmation",
        expected_range_pct=PercentRange(ZERO, ZERO),
        loss_range_pct=PercentRange(ZERO, ZERO),
        rationale=LocalizedText(
            "Ожидание предпочтительнее направленной идеи при недостаточной доказательной базе.",
            "Waiting is preferable to a directional idea when evidence is insufficient.",
        ),
        evidence=reason_items,
        limitations=(
            LocalizedText(
                "Это общая аналитика без исполнения сделок и персональной рекомендации.",
                "This is general analytics without trade execution or personalized advice.",
            ),
        ),
    )


def generate_opportunities(
    context: OpportunityContext,
    *,
    max_ideas: int = 10,
    minimum_score: Decimal = Decimal("0.55"),
) -> tuple[OpportunityIdea, ...]:
    """Build deterministic, non-executable ideas from already structured market inputs."""

    if max_ideas < 1 or max_ideas > 10:
        raise ValueError("max_ideas must be between 1 and 10")
    _unit_interval(minimum_score, "minimum_score")
    if context.data_quality_score < Decimal("0.50"):
        return (
            _wait_idea(
                (
                    LocalizedText(
                        "Общее качество данных ниже минимального порога 0.50.",
                        "Overall data quality is below the minimum 0.50 threshold.",
                    ),
                )
            ),
        )

    scenarios = {item.code: item for item in sorted(context.scenarios, key=lambda item: item.code)}
    quotes = sorted(
        context.quotes,
        key=lambda item: (item.symbol, item.asset_class.value, sorted(item.exposures)),
    )
    rejected_stale = 0
    rejected_options = 0
    candidates: list[tuple[Decimal, _Template, ScenarioSignal, MarketQuote]] = []
    for template in _TEMPLATES:
        scenario = scenarios.get(template.scenario_code)
        if scenario is None or scenario.status in {"inactive", "watch"}:
            continue
        if scenario.confidence == "low":
            continue
        for quote in quotes:
            if quote.asset_class not in template.asset_classes or template.exposure not in quote.exposures:
                continue
            if _is_stale(context, quote) or quote.data_quality_score < Decimal("0.50"):
                rejected_stale += 1
                continue
            if quote.asset_class is AssetClass.OPTIONS and quote.option_risk_profile != "defined_risk":
                rejected_options += 1
                continue
            score = _score(context, scenario, quote)
            if score >= minimum_score:
                candidates.append((score, template, scenario, quote))

    candidates.sort(
        key=lambda item: (
            -item[0],
            item[1].scenario_code,
            item[3].symbol,
            item[1].strategy,
            item[1].side.value,
        )
    )
    if not candidates:
        reasons = []
        if rejected_stale:
            reasons.append(
                LocalizedText(
                    f"Отклонено устаревших или слабых котировок: {rejected_stale}.",
                    f"Stale or weak quotes rejected: {rejected_stale}.",
                )
            )
        if rejected_options:
            reasons.append(
                LocalizedText(
                    f"Отклонено опционных конструкций без ограниченного риска: {rejected_options}.",
                    f"Option structures without defined risk rejected: {rejected_options}.",
                )
            )
        return (_wait_idea(reasons),)

    ideas = []
    for rank, (score, template, scenario, quote) in enumerate(candidates[:max_ideas], start=1):
        expected, loss = _ranges(quote)
        limitations = [
            LocalizedText(
                "Диапазоны являются сценарной оценкой из входных данных, а не прогнозом доходности.",
                "Ranges are scenario estimates derived from inputs, not return forecasts.",
            ),
            LocalizedText(
                "Это общая аналитика без исполнения сделок и персональной рекомендации.",
                "This is general analytics without trade execution or personalized advice.",
            ),
        ]
        if quote.asset_class is AssetClass.OPTIONS:
            limitations.append(
                LocalizedText(
                    "Рассматривается только конструкция с заранее ограниченным максимальным убытком.",
                    "Only a structure with a predefined maximum loss is considered.",
                )
            )
        ideas.append(
            OpportunityIdea(
                rank=rank,
                idea_key=(
                    f"{template.scenario_code}:{quote.asset_class.value}:"
                    f"{quote.symbol}:{template.strategy}"
                ),
                symbol=quote.symbol,
                asset_class=quote.asset_class,
                side=template.side,
                strategy=template.strategy,
                score=score,
                trigger=LocalizedText(
                    f"Сценарий {scenario.code} остаётся {scenario.status}; котировка {quote.symbol} свежая.",
                    f"Scenario {scenario.code} remains {scenario.status}; {quote.symbol} quote is fresh.",
                ),
                invalidation=LocalizedText(
                    f"Сценарий {scenario.code} уходит ниже elevated или данные {quote.symbol} устаревают.",
                    f"Scenario {scenario.code} falls below elevated or {quote.symbol} data becomes stale.",
                ),
                horizon=scenario.horizon,
                expected_range_pct=expected,
                loss_range_pct=loss,
                rationale=template.rationale,
                evidence=(
                    LocalizedText(
                        f"Статус сценария: {scenario.status}; уверенность: {scenario.confidence}.",
                        f"Scenario status: {scenario.status}; confidence: {scenario.confidence}.",
                    ),
                    LocalizedText(
                        f"Ликвидность {quote.liquidity_score}; качество данных {quote.data_quality_score}.",
                        f"Liquidity {quote.liquidity_score}; data quality {quote.data_quality_score}.",
                    ),
                ),
                limitations=tuple(limitations),
            )
        )
    return tuple(ideas)
