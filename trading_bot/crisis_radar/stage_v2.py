from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN

from trading_bot.crisis_radar.domain import CoverageStatus, IndicatorBand
from trading_bot.crisis_radar.scoring_v2 import (
    INDICATOR_BAND_THRESHOLDS,
    IndicatorScoreV2,
    band_from_score,
)


STAGE_VERSION = "independent-stage-v2-seed-1"
DEPENDENCY_GRAPH_VERSION = "dependency-graph-v2-seed-1"
ZERO = Decimal("0")
ONE = Decimal("1")
ACTIVE_SCORE_THRESHOLD = Decimal(".25")
GROUP_SCORE_WEIGHTS = {
    "central": Decimal(".35"),
    "top_two": Decimal(".30"),
    "breadth": Decimal(".20"),
    "dynamics": Decimal(".15"),
}
INTENSITY_WEIGHTS = {
    "active_cluster_mean": Decimal(".60"),
    "top_two_cluster_mean": Decimal(".40"),
}
BREADTH_WEIGHTS = {
    "clusters": Decimal(".50"),
    "regions": Decimal(".25"),
    "anchors": Decimal(".25"),
}
STAGE_GATES = {
    "tension": {
        "intensity": Decimal("25"),
        "breadth": Decimal("20"),
        "clusters": 1,
    },
    "warning": {
        "intensity": Decimal("40"),
        "breadth": Decimal("35"),
        "clusters": 2,
    },
    "confirmation": {
        "intensity": Decimal("60"),
        "breadth": Decimal("50"),
        "clusters": 3,
        "anchor_confirmation": True,
    },
    "crisis": {
        "intensity": Decimal("75"),
        "breadth": Decimal("65"),
        "clusters": 4,
        "regions": 2,
        "critical_anchor": True,
    },
}
RECOVERY_INTENSITY_DROP = Decimal("15")
RECOVERY_MAX_BREADTH = Decimal("50")


@dataclass(frozen=True)
class DependencyAssignment:
    indicator_code: str
    group_code: str
    subchannel_code: str
    cluster_code: str
    region_code: str
    anchor_class: str | None


@dataclass(frozen=True)
class GroupScoreV2:
    group_code: str
    cluster_code: str
    score: Decimal
    band: IndicatorBand
    subchannel_count: int
    active_subchannel_count: int
    thin_group: bool
    contributors: tuple[str, ...]


@dataclass(frozen=True)
class MarketStageV2:
    stage: str
    calculated_stage: str
    stress_intensity: Decimal
    systemic_breadth: Decimal
    active_independent_clusters: int
    active_regions: int
    anchor_confirmation: bool
    critical_anchor: bool
    groups: tuple[GroupScoreV2, ...]
    input_checksum: str
    reasons: tuple[str, ...]


def dependency_for(*, code: str, group_code: str, region_code: str) -> DependencyAssignment:
    if group_code in {"labor"}:
        cluster, anchor = "labor", None
        subchannel = (
            "claims" if "claims" in code or code == "sahm_rule"
            else "payrolls" if "payroll" in code
            else "vacancies_turnover" if "openings" in code or "quits" in code
            else "leading_labor" if "temporary" in code or "weekly_hours" in code
            else group_code
        )
    elif group_code in {"credit", "global_credit_cycle"} or group_code.endswith("_credit_cycle"):
        cluster, anchor = "corporate_credit", "credit"
        subchannel = "corporate_spreads" if code in {"us_hy_oas", "us_ig_oas"} else group_code
    elif group_code in {
        "rates_liquidity", "us_financial_conditions", "euro_financial_stress",
        "banking_stress", "dollar_liquidity",
    }:
        cluster, anchor = "dollar_liquidity_banks", "liquidity"
        subchannel = (
            "bank_deposits" if "deposits" in code
            else "emergency_borrowing" if "primary_credit" in code
            else "yield_curve" if "10y2y" in code
            else "real_yields" if "real_yield" in code
            else "central_bank_balance_sheet" if "fed_assets" in code
            else "broad_dollar" if "broad_usd" in code
            else group_code
        )
    elif group_code in {"market_stress", "equity_market_stress"} or group_code.endswith("_market_conditions"):
        cluster, anchor = "markets_fx", "market"
        subchannel = (
            "implied_volatility" if code == "vix"
            else "financial_stress_composite" if "financial_stress_index" in code
            else "equity_drawdown" if "drawdown" in code
            else group_code
        )
    elif group_code in {"crypto_leverage", "crypto_price_stress"}:
        cluster, anchor = "crypto_stablecoins", "market"
        subchannel = (
            "funding" if "funding" in code
            else "open_interest" if "oi_" in code
            else "crypto_price" if "drawdown" in code
            else group_code
        )
    elif group_code == "inflation_commodities":
        cluster, anchor = "commodities_supply", None
        subchannel = group_code
    elif group_code == "housing_cre":
        cluster, anchor = "housing_cre", None
        subchannel = "housing_activity"
    elif group_code in {"real_economy", "global_growth", "euro_growth", "china_growth"} or group_code.endswith("_growth"):
        cluster, anchor = "real_economy", None
        subchannel = group_code
    elif group_code == "global_leading_cycle" or group_code.endswith("_leading_cycle"):
        cluster, anchor = "leading_cycle", None
        subchannel = group_code
    else:
        cluster, anchor = group_code, None
        subchannel = group_code
    return DependencyAssignment(
        indicator_code=code,
        group_code=group_code,
        subchannel_code=subchannel,
        cluster_code=cluster,
        region_code=region_code,
        anchor_class=anchor,
    )


def _quantize(value: Decimal) -> Decimal:
    return max(ZERO, min(Decimal("100"), value)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_EVEN
    )


def aggregate_groups_v2(
    scores: tuple[IndicatorScoreV2, ...],
    assignments: dict[str, DependencyAssignment],
) -> tuple[GroupScoreV2, ...]:
    grouped: dict[str, list[IndicatorScoreV2]] = {}
    for score in scores:
        if score.effective_score is None or score.indicator_code not in assignments:
            continue
        grouped.setdefault(assignments[score.indicator_code].group_code, []).append(score)
    result = []
    for group_code, items in sorted(grouped.items()):
        by_subchannel: dict[str, list[IndicatorScoreV2]] = {}
        for item in items:
            by_subchannel.setdefault(assignments[item.indicator_code].subchannel_code, []).append(item)
        subchannel_scores = sorted(
            (
                max(value.effective_score or ZERO for value in values)
                for values in by_subchannel.values()
            ),
            reverse=True,
        )
        central = sum(subchannel_scores) / Decimal(len(subchannel_scores))
        top_two = sum(subchannel_scores[:2]) / Decimal(min(2, len(subchannel_scores)))
        breadth = Decimal(
            sum(value >= ACTIVE_SCORE_THRESHOLD for value in subchannel_scores)
        ) / Decimal(len(subchannel_scores))
        dynamics = sum(
            max(item.acceleration_score, item.persistence_score) for item in items
        ) / Decimal(len(items))
        raw = (
            central * GROUP_SCORE_WEIGHTS["central"]
            + top_two * GROUP_SCORE_WEIGHTS["top_two"]
            + breadth * GROUP_SCORE_WEIGHTS["breadth"]
            + dynamics * GROUP_SCORE_WEIGHTS["dynamics"]
        )
        if all(value.economic_score < ACTIVE_SCORE_THRESHOLD for value in items) and all(
            value.agreement.value != "early_anomaly" for value in items
        ):
            raw = min(raw, ACTIVE_SCORE_THRESHOLD)
        score = min(ONE, max(ZERO, raw)).quantize(Decimal("0.0001"))
        assignment = assignments[items[0].indicator_code]
        result.append(
            GroupScoreV2(
                group_code=group_code,
                cluster_code=assignment.cluster_code,
                score=score,
                band=band_from_score(score),
                subchannel_count=len(subchannel_scores),
                active_subchannel_count=sum(
                    value >= ACTIVE_SCORE_THRESHOLD for value in subchannel_scores
                ),
                thin_group=len(subchannel_scores) == 1,
                contributors=tuple(
                    item.indicator_code
                    for item in sorted(items, key=lambda value: value.effective_score or ZERO, reverse=True)[:5]
                ),
            )
        )
    return tuple(result)


def calculate_stage_v2(
    scores: tuple[IndicatorScoreV2, ...],
    assignments: dict[str, DependencyAssignment],
    *,
    coverage_status: CoverageStatus | None,
    previous_stage: str | None = None,
    previous_peak_intensity: Decimal | None = None,
) -> MarketStageV2:
    groups = aggregate_groups_v2(scores, assignments)
    cluster_scores: dict[str, Decimal] = {}
    for group in groups:
        cluster_scores[group.cluster_code] = max(
            cluster_scores.get(group.cluster_code, ZERO), group.score
        )
    active = [value for value in cluster_scores.values() if value >= ACTIVE_SCORE_THRESHOLD]
    if active:
        mean_active = sum(active) / Decimal(len(active))
        top = sorted(active, reverse=True)[:2]
        mean_top = sum(top) / Decimal(len(top))
        intensity = _quantize(
            (
                mean_active * INTENSITY_WEIGHTS["active_cluster_mean"]
                + mean_top * INTENSITY_WEIGHTS["top_two_cluster_mean"]
            )
            * 100
        )
    else:
        intensity = ZERO

    eligible_clusters = max(1, len(set(item.cluster_code for item in assignments.values())))
    regions = {item.region_code for item in assignments.values()}
    active_codes = {
        code for code, value in cluster_scores.items() if value >= ACTIVE_SCORE_THRESHOLD
    }
    active_regions = {
        assignment.region_code
        for score in scores
        if score.effective_score is not None
        and score.effective_score >= ACTIVE_SCORE_THRESHOLD
        and (assignment := assignments.get(score.indicator_code)) is not None
    }
    anchor_scores: dict[str, Decimal] = {}
    for score in scores:
        assignment = assignments.get(score.indicator_code)
        if assignment is None or assignment.anchor_class is None or score.effective_score is None:
            continue
        anchor_scores[assignment.anchor_class] = max(
            anchor_scores.get(assignment.anchor_class, ZERO), score.effective_score
        )
    active_anchor_classes = sum(
        value >= ACTIVE_SCORE_THRESHOLD for value in anchor_scores.values()
    )
    breadth = _quantize(
        Decimal("100")
        * (
            BREADTH_WEIGHTS["clusters"]
            * Decimal(len(active_codes))
            / Decimal(eligible_clusters)
            + BREADTH_WEIGHTS["regions"]
            * Decimal(len(active_regions))
            / Decimal(max(1, len(regions)))
            + BREADTH_WEIGHTS["anchors"]
            * Decimal(active_anchor_classes)
            / Decimal("3")
        )
    )
    anchor_confirmation = active_anchor_classes > 0
    critical_anchor = any(
        value >= INDICATOR_BAND_THRESHOLDS["critical"]
        for value in anchor_scores.values()
    )
    cluster_count = len(active_codes)
    calculated = "stable"
    reasons = []
    if (
        intensity >= STAGE_GATES["crisis"]["intensity"]
        and breadth >= STAGE_GATES["crisis"]["breadth"]
        and cluster_count >= STAGE_GATES["crisis"]["clusters"]
        and len(active_regions) >= STAGE_GATES["crisis"]["regions"]
        and critical_anchor
    ):
        calculated = "crisis"
    elif (
        intensity >= STAGE_GATES["confirmation"]["intensity"]
        and breadth >= STAGE_GATES["confirmation"]["breadth"]
        and cluster_count >= STAGE_GATES["confirmation"]["clusters"]
        and anchor_confirmation
    ):
        calculated = "confirmation"
    elif (
        intensity >= STAGE_GATES["warning"]["intensity"]
        and breadth >= STAGE_GATES["warning"]["breadth"]
        and cluster_count >= STAGE_GATES["warning"]["clusters"]
    ):
        calculated = "warning"
    elif (
        intensity >= STAGE_GATES["tension"]["intensity"]
        or breadth >= STAGE_GATES["tension"]["breadth"]
    ) and cluster_count >= STAGE_GATES["tension"]["clusters"]:
        calculated = "tension"
    peak = previous_peak_intensity or ZERO
    if (
        previous_stage in {"confirmation", "crisis", "recovery"}
        and peak - intensity >= RECOVERY_INTENSITY_DROP
        and breadth < RECOVERY_MAX_BREADTH
    ):
        calculated = "recovery"
        reasons.append("stress_receded_from_confirmed_peak")
    stage = (
        "insufficient_data"
        if coverage_status is CoverageStatus.INSUFFICIENT_DATA
        else calculated
    )
    if stage == "insufficient_data":
        reasons.append("numeric_coverage_insufficient")
    canonical = {
        "version": STAGE_VERSION,
        "scores": {item.indicator_code: item.input_checksum for item in scores},
        "assignments": {code: assignment.__dict__ for code, assignment in assignments.items()},
        "coverage": None if coverage_status is None else coverage_status.value,
        "previous_stage": previous_stage,
        "previous_peak_intensity": None if previous_peak_intensity is None else str(previous_peak_intensity),
    }
    checksum = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return MarketStageV2(
        stage=stage,
        calculated_stage=calculated,
        stress_intensity=intensity,
        systemic_breadth=breadth,
        active_independent_clusters=cluster_count,
        active_regions=len(active_regions),
        anchor_confirmation=anchor_confirmation,
        critical_anchor=critical_anchor,
        groups=groups,
        input_checksum=checksum,
        reasons=tuple(reasons),
    )
