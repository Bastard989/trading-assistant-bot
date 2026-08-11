from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import timezone
from decimal import Decimal

from trading_bot.crisis_radar.backtest import BacktestResult, ScenarioEvent, SignalPoint
from trading_bot.crisis_radar.domain import (
    CoverageStatus,
    DataFreshness,
    IndicatorBand,
    IndicatorState,
    IndicatorThresholds,
    MarketOverview,
    Observation,
    QualityFlag,
    RiskDirection,
    ScenarioState,
)
from trading_bot.crisis_radar.news import NewsEvidence, NewsItem
from trading_bot.crisis_radar.event_pipeline import (
    EVENT_RULE_VERSION,
    EventCandidate,
    event_score,
    near_duplicate_score,
)
from trading_bot.crisis_radar.event_catalog import EventCatalogVersion
from trading_bot.crisis_radar.stability import STABILITY_POLICY
from trading_bot.crisis_radar.scenario_fusion import (
    FUSION_VERSION,
    ScenarioFusionState,
    fusion_payload,
)
from trading_bot.crisis_radar.scoring_v2 import SCORING_VERSION, IndicatorScoreV2
from trading_bot.crisis_radar.scenario_v2 import (
    PLAYBOOK_VERSION,
    ScenarioStateV2,
    playbook_for,
)
from trading_bot.crisis_radar.stage_v2 import (
    STAGE_VERSION,
    DependencyAssignment,
    MarketStageV2,
)
from trading_bot.crisis_radar.trends import (
    FEATURE_VERSION,
    ContagionFeatures,
    IndicatorFeatures,
    TimePoint,
    feature_payload,
)
from trading_bot.db import Database


@dataclass(frozen=True)
class SavedObservation:
    observation_id: int
    inserted: bool
    revision_created: bool


@dataclass(frozen=True)
class SavedNewsItem:
    news_item_id: int
    inserted: bool
    updated: bool


@dataclass(frozen=True)
class AnalysisInput:
    methodology_id: int
    observation_id: int
    group_code: str
    max_staleness_seconds: int
    frequency: str
    thresholds: IndicatorThresholds
    observation: Observation


@dataclass(frozen=True)
class AlertDelivery:
    delivery_id: int
    user_id: int
    event_type: str
    severity: str
    scenario_code: str
    from_state: str
    to_state: str
    payload: dict


@dataclass(frozen=True)
class ReportDelivery:
    delivery_id: int
    user_id: int
    report_type: str
    report_date: str
    payload: dict


@dataclass(frozen=True)
class DataHealthDelivery:
    delivery_id: int
    user_id: int
    from_status: str
    to_status: str
    payload: dict


def _utc_text(value) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _analysis_inputs_from_rows(rows) -> list[AnalysisInput]:
    result: list[AnalysisInput] = []
    known_flags = {item.value: item for item in QualityFlag}
    for row in rows:
        flags = frozenset(
            known_flags[item]
            for item in json.loads(row["quality_flags"] or "[]")
            if item in known_flags
        )
        observation = Observation(
            indicator_code=row["indicator_code"],
            source_code=row["source_code"],
            value=Decimal(row["value_text"]),
            unit=row["unit"],
            observed_at=datetime.fromisoformat(row["observed_at"]),
            released_at=datetime.fromisoformat(row["released_at"]),
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            vintage=row["vintage"],
            quality_flags=flags,
            content_hash=row["content_hash"],
        )
        result.append(
            AnalysisInput(
                methodology_id=int(row["methodology_id"]),
                observation_id=int(row["observation_id"]),
                group_code=row["group_code"],
                max_staleness_seconds=int(row["max_staleness_seconds"]),
                frequency=row["frequency"],
                thresholds=IndicatorThresholds(
                    warning=Decimal(row["warning_value"]),
                    danger=Decimal(row["danger_value"]),
                    critical=Decimal(row["critical_value"]),
                    reference=Decimal(row["reference_value"]),
                    direction=RiskDirection(row["risk_direction"]),
                ),
                observation=observation,
            )
        )
    return result


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


_REGION_CODES = ("US", "EU", "CHINA", "GLOBAL")
_REGION_NAMES = {
    "US": {"ru": "США", "en": "United States"},
    "EU": {"ru": "Европа", "en": "Europe"},
    "CHINA": {"ru": "Китай", "en": "China"},
    "GLOBAL": {"ru": "Мир", "en": "Global"},
}
_GROUP_NAMES = {
    "labor": {"ru": "Рынок труда", "en": "Labor market"},
    "credit": {"ru": "Кредит", "en": "Credit"},
    "market_stress": {"ru": "Рыночный стресс", "en": "Market stress"},
    "equity_market_stress": {"ru": "Стресс рынка акций", "en": "Equity market stress"},
    "rates_liquidity": {"ru": "Ставки и ликвидность", "en": "Rates and liquidity"},
    "us_financial_conditions": {"ru": "Финансовые условия США", "en": "US financial conditions"},
    "real_economy": {"ru": "Реальная экономика", "en": "Real economy"},
    "inflation_commodities": {"ru": "Инфляция и сырьё", "en": "Inflation and commodities"},
    "euro_financial_stress": {"ru": "Финансовый стресс еврозоны", "en": "Euro-area financial stress"},
    "euro_growth": {"ru": "Рост экономики еврозоны", "en": "Euro-area growth"},
    "china_growth": {"ru": "Рост экономики Китая", "en": "China growth"},
    "china_leading_cycle": {"ru": "Опережающий цикл Китая", "en": "China leading cycle"},
    "global_growth": {"ru": "Мировой экономический рост", "en": "Global growth"},
    "global_credit_cycle": {"ru": "Глобальный кредитный цикл", "en": "Global credit cycle"},
    "global_leading_cycle": {"ru": "Опережающий цикл G20", "en": "G20 leading cycle"},
    "crypto_leverage": {"ru": "Криптовалютные плечи", "en": "Crypto leverage"},
    "crypto_price_stress": {"ru": "Ценовой стресс крипторынка", "en": "Crypto price stress"},
}


def _normalized_region(region_code: str) -> str:
    if region_code == "US":
        return "US"
    if region_code in {"EU", "EA20"}:
        return "EU"
    if region_code in {"CN", "CHN"}:
        return "CHINA"
    return "GLOBAL"


def _freshness_from_release(
    released_at: str | None,
    *,
    as_of: datetime,
    max_staleness_seconds: int,
) -> tuple[str, int | None]:
    if released_at is None:
        return DataFreshness.MISSING.value, None
    released = datetime.fromisoformat(released_at)
    age_seconds = int((as_of - released).total_seconds())
    if age_seconds < 0:
        return DataFreshness.MISSING.value, None
    if age_seconds <= max_staleness_seconds:
        return DataFreshness.FRESH.value, age_seconds
    if age_seconds <= int(max_staleness_seconds * 1.5):
        return DataFreshness.DELAYED.value, age_seconds
    return DataFreshness.STALE.value, age_seconds


_SENSITIVE_ERROR = re.compile(
    r"(?i)(api[_-]?key|token|secret|authorization|password)(\s*[:=]\s*)([^\s,;&]+)"
)
_URL_QUERY = re.compile(r"(https?://[^\s?]+)\?[^\s]+", re.IGNORECASE)


def _sanitized_error(detail: str | None) -> str:
    if not detail:
        return ""
    value = _URL_QUERY.sub(r"\1?[redacted]", str(detail))
    value = _SENSITIVE_ERROR.sub(r"\1\2[redacted]", value)
    return value.replace("\r", " ").replace("\n", " ")[:300]


def _backtest_metrics_payload(result: BacktestResult) -> dict:
    metrics = result.metrics
    return {
        "sample_count": metrics.sample_count,
        "scored_count": metrics.scored_count,
        "positive_count": metrics.positive_count,
        "positive_event_count": metrics.positive_event_count,
        "coverage": _decimal_text(metrics.coverage),
        "brier_score": _decimal_text(metrics.brier_score),
        "baseline_brier_score": _decimal_text(metrics.baseline_brier_score),
        "log_loss": _decimal_text(metrics.log_loss),
        "precision": _decimal_text(metrics.precision),
        "recall": _decimal_text(metrics.recall),
        "false_alert_rate": _decimal_text(metrics.false_alert_rate),
        "average_lead_days": _decimal_text(metrics.average_lead_days),
    }


class CrisisRadarRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def register_source(
        self,
        code: str,
        name: str,
        *,
        base_url: str = "",
        terms_url: str = "",
        access_type: str = "api",
        expected_frequency: str = "",
        max_staleness_seconds: int = 86400,
    ) -> int:
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO cr_sources(
                    code, name, base_url, terms_url, access_type,
                    expected_frequency, max_staleness_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name=excluded.name,
                    base_url=excluded.base_url,
                    terms_url=excluded.terms_url,
                    access_type=excluded.access_type,
                    expected_frequency=excluded.expected_frequency,
                    max_staleness_seconds=excluded.max_staleness_seconds,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    code,
                    name,
                    base_url,
                    terms_url,
                    access_type,
                    expected_frequency,
                    max_staleness_seconds,
                ),
            )
            return int(connection.execute("SELECT id FROM cr_sources WHERE code = ?", (code,)).fetchone()[0])

    def start_sync_run(self, source_code: str, *, started_at: datetime) -> int:
        with self.db.connect() as connection:
            source = connection.execute("SELECT id FROM cr_sources WHERE code = ?", (source_code,)).fetchone()
            if source is None:
                raise ValueError(f"unknown Crisis Radar source: {source_code}")
            cursor = connection.execute(
                "INSERT INTO cr_sync_runs(source_id, started_at, status) VALUES (?, ?, 'running')",
                (source[0], _utc_text(started_at)),
            )
            return int(cursor.lastrowid)

    def finish_sync_run(
        self,
        sync_run_id: int,
        *,
        finished_at: datetime,
        status: str,
        rows_fetched: int,
        rows_written: int,
        error_code: str = "",
        error_detail: str = "",
    ) -> None:
        if status not in {"succeeded", "partial", "failed"}:
            raise ValueError("finished sync status must be succeeded, partial or failed")
        with self.db.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE cr_sync_runs
                SET finished_at = ?, status = ?, rows_fetched = ?, rows_written = ?,
                    error_code = ?, error_detail = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    _utc_text(finished_at),
                    status,
                    rows_fetched,
                    rows_written,
                    error_code[:80],
                    error_detail[:1000],
                    sync_run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("sync run is missing or already finished")

    def register_methodology(
        self,
        code: str,
        version: str,
        *,
        checksum: str,
        effective_from: str,
    ) -> int:
        with self.db.connect() as connection:
            existing = connection.execute(
                "SELECT id, checksum FROM cr_methodology_versions WHERE code = ? AND version = ?",
                (code, version),
            ).fetchone()
            if existing is not None:
                if existing[1] != checksum:
                    raise RuntimeError(f"Crisis Radar methodology checksum mismatch for {code}:{version}")
                return int(existing[0])
            cursor = connection.execute(
                """
                INSERT INTO cr_methodology_versions(code, version, checksum, effective_from)
                VALUES (?, ?, ?, ?)
                """,
                (code, version, checksum, effective_from),
            )
            return int(cursor.lastrowid)

    def register_indicator(
        self,
        code: str,
        name: str,
        *,
        group_code: str,
        unit: str,
        frequency: str,
        risk_direction: str,
        source_code: str,
        region_code: str = "GLOBAL",
        provider_series_id: str = "",
        transform: str = "identity",
        max_staleness_seconds: int = 86400,
        enabled: bool = True,
    ) -> int:
        with self.db.connect() as connection:
            source = connection.execute("SELECT id FROM cr_sources WHERE code = ?", (source_code,)).fetchone()
            if source is None:
                raise ValueError(f"unknown Crisis Radar source: {source_code}")
            connection.execute(
                """
                INSERT INTO cr_indicator_definitions(
                    code, name, group_code, region_code, unit, frequency,
                    risk_direction, source_id, provider_series_id, transform,
                    max_staleness_seconds, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name=excluded.name,
                    group_code=excluded.group_code,
                    region_code=excluded.region_code,
                    unit=excluded.unit,
                    frequency=excluded.frequency,
                    risk_direction=excluded.risk_direction,
                    source_id=excluded.source_id,
                    provider_series_id=excluded.provider_series_id,
                    transform=excluded.transform,
                    max_staleness_seconds=excluded.max_staleness_seconds,
                    enabled=excluded.enabled,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    code,
                    name,
                    group_code,
                    region_code,
                    unit,
                    frequency,
                    risk_direction,
                    source[0],
                    provider_series_id,
                    transform,
                    max_staleness_seconds,
                    int(enabled),
                ),
            )
            return int(
                connection.execute(
                    "SELECT id FROM cr_indicator_definitions WHERE code = ?", (code,)
                ).fetchone()[0]
            )

    def register_thresholds(
        self,
        indicator_id: int,
        methodology_id: int,
        thresholds: IndicatorThresholds,
        *,
        scope: str = "system",
        owner_user_id: int = 0,
        basis: str = "legacy",
        promotion_status: str = "active",
        rationale: dict | None = None,
        source_url: str = "",
        operational_role: str = "",
        profile: str = "",
        promotion_evidence: dict | None = None,
        introduced_at: str = "",
        retired_at: str | None = None,
        metadata_checksum: str | None = None,
    ) -> int:
        if basis not in {"legacy", "official", "empirical", "heuristic", "hybrid"}:
            raise ValueError("unknown threshold basis")
        if promotion_status not in {"candidate", "active", "rejected", "retired"}:
            raise ValueError("unknown threshold promotion status")
        rationale_payload = json.dumps(
            rationale or {}, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        promotion_evidence_payload = json.dumps(
            promotion_evidence or {}, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        metadata = {
            "basis": basis,
            "promotion_status": promotion_status,
            "rationale": json.loads(rationale_payload),
            "source_url": source_url,
            "operational_role": operational_role,
            "profile": profile,
            "promotion_evidence": json.loads(promotion_evidence_payload),
            "introduced_at": introduced_at,
            "retired_at": retired_at,
        }
        resolved_checksum = metadata_checksum or hashlib.sha256(
            json.dumps(metadata, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if len(resolved_checksum) != 64:
            raise ValueError("threshold metadata checksum must contain 64 characters")
        values = (
            format(thresholds.warning, "f"),
            format(thresholds.danger, "f"),
            format(thresholds.critical, "f"),
            format(thresholds.reference, "f"),
        )
        with self.db.connect() as connection:
            existing = connection.execute(
                """
                SELECT id, warning_value, danger_value, critical_value, reference_value,
                       basis, promotion_status, rationale_payload, source_url,
                       operational_role, profile, promotion_evidence_payload,
                       introduced_at, retired_at, metadata_checksum
                FROM cr_threshold_sets
                WHERE indicator_id = ? AND methodology_id = ? AND scope = ? AND owner_user_id = ?
                """,
                (indicator_id, methodology_id, scope, owner_user_id),
            ).fetchone()
            if existing is not None:
                existing_values = tuple(existing[index] for index in range(1, 5))
                existing_metadata = tuple(existing[index] for index in range(5, 15))
                expected_metadata = (
                    basis,
                    promotion_status,
                    rationale_payload,
                    source_url,
                    operational_role,
                    profile,
                    promotion_evidence_payload,
                    introduced_at,
                    retired_at,
                    resolved_checksum,
                )
                if existing_values != values:
                    raise RuntimeError("threshold values changed inside an immutable methodology")
                if existing_metadata != expected_metadata:
                    if existing[14] != "":
                        raise RuntimeError(
                            "threshold metadata changed inside an immutable methodology"
                        )
                    connection.execute(
                        """
                        UPDATE cr_threshold_sets
                        SET basis=?, promotion_status=?, rationale_payload=?, source_url=?,
                            operational_role=?, profile=?, promotion_evidence_payload=?,
                            introduced_at=?, retired_at=?, metadata_checksum=?
                        WHERE id=?
                        """,
                        (*expected_metadata, int(existing[0])),
                    )
                return int(existing[0])
            cursor = connection.execute(
                """
                INSERT INTO cr_threshold_sets(
                    indicator_id, methodology_id, scope, owner_user_id,
                    warning_value, danger_value, critical_value, reference_value,
                    basis, promotion_status, rationale_payload, source_url,
                    operational_role, profile, promotion_evidence_payload,
                    introduced_at, retired_at, metadata_checksum
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    indicator_id,
                    methodology_id,
                    scope,
                    owner_user_id,
                    *values,
                    basis,
                    promotion_status,
                    rationale_payload,
                    source_url,
                    operational_role,
                    profile,
                    promotion_evidence_payload,
                    introduced_at,
                    retired_at,
                    resolved_checksum,
                ),
            )
            return int(cursor.lastrowid)

    def register_entity_metadata(
        self,
        *,
        entity_type: str,
        entity_code: str,
        metadata_version: str,
        payload: dict,
    ) -> int:
        required = (
            "name_ru", "name_en", "short_name_ru", "short_name_en",
            "description_ru", "description_en", "why_it_matters_ru",
            "why_it_matters_en", "worse_when_ru", "worse_when_en",
            "calculation_ru", "calculation_en", "limitations_ru",
            "limitations_en", "source_name", "technical_code",
        )
        if entity_type not in {"indicator", "group", "scenario", "status", "section"}:
            raise ValueError("unknown metadata entity type")
        if any(not str(payload.get(key, "")).strip() for key in required if key != "source_name"):
            raise ValueError("entity metadata is incomplete")
        values = tuple(str(payload.get(key, "")) for key in required)
        checksum = hashlib.sha256(
            json.dumps(
                {"entity_type": entity_type, "entity_code": entity_code,
                 "metadata_version": metadata_version, "payload": dict(zip(required, values))},
                ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with self.db.connect() as connection:
            existing = connection.execute(
                """
                SELECT id, checksum FROM cr_entity_metadata
                WHERE entity_type=? AND entity_code=? AND metadata_version=?
                """,
                (entity_type, entity_code, metadata_version),
            ).fetchone()
            if existing is not None:
                if existing["checksum"] != checksum:
                    raise RuntimeError("entity metadata changed inside an immutable version")
                return int(existing["id"])
            cursor = connection.execute(
                """
                INSERT INTO cr_entity_metadata(
                    entity_type, entity_code, metadata_version,
                    name_ru, name_en, short_name_ru, short_name_en,
                    description_ru, description_en, why_it_matters_ru,
                    why_it_matters_en, worse_when_ru, worse_when_en,
                    calculation_ru, calculation_en, limitations_ru,
                    limitations_en, source_name, technical_code, checksum
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (entity_type, entity_code, metadata_version, *values, checksum),
            )
            return int(cursor.lastrowid)

    def register_dependency_assignment(
        self,
        *,
        methodology_id: int,
        assignment: DependencyAssignment,
        graph_version: str,
    ) -> int:
        canonical = json.dumps(
            {**assignment.__dict__, "graph_version": graph_version},
            sort_keys=True, separators=(",", ":"),
        )
        checksum = hashlib.sha256(canonical.encode()).hexdigest()
        with self.db.connect() as connection:
            indicator = connection.execute(
                "SELECT id FROM cr_indicator_definitions WHERE code=?",
                (assignment.indicator_code,),
            ).fetchone()
            if indicator is None:
                raise ValueError("unknown dependency indicator")
            existing = connection.execute(
                """
                SELECT id, checksum FROM cr_dependency_assignments
                WHERE methodology_id=? AND indicator_id=? AND graph_version=?
                """,
                (methodology_id, indicator["id"], graph_version),
            ).fetchone()
            if existing is not None:
                if existing["checksum"] != checksum:
                    raise RuntimeError("dependency assignment changed inside immutable graph")
                return int(existing["id"])
            cursor = connection.execute(
                """
                INSERT INTO cr_dependency_assignments(
                    methodology_id, indicator_id, graph_version, subchannel_code,
                    group_code, cluster_code, region_code, anchor_class, checksum
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    methodology_id, indicator["id"], graph_version,
                    assignment.subchannel_code, assignment.group_code,
                    assignment.cluster_code, assignment.region_code,
                    assignment.anchor_class, checksum,
                ),
            )
            return int(cursor.lastrowid)

    def upsert_personal_thresholds(
        self,
        indicator_code: str,
        *,
        methodology_code: str,
        methodology_version: str,
        owner_user_id: int,
        thresholds: IndicatorThresholds,
    ) -> int:
        if owner_user_id <= 0:
            raise ValueError("personal threshold owner must be positive")
        with self.db.connect() as connection:
            resolved = connection.execute(
                """
                SELECT indicator.id AS indicator_id, methodology.id AS methodology_id
                FROM cr_indicator_definitions AS indicator
                JOIN cr_methodology_versions AS methodology
                  ON methodology.code = ? AND methodology.version = ?
                WHERE indicator.code = ? AND indicator.enabled = 1
                """,
                (methodology_code, methodology_version, indicator_code),
            ).fetchone()
            if resolved is None:
                raise ValueError("unknown indicator or methodology")
            values = (
                format(thresholds.warning, "f"),
                format(thresholds.danger, "f"),
                format(thresholds.critical, "f"),
                format(thresholds.reference, "f"),
            )
            connection.execute(
                """
                INSERT INTO cr_threshold_sets(
                    indicator_id, methodology_id, scope, owner_user_id,
                    warning_value, danger_value, critical_value, reference_value,
                    basis, promotion_status, rationale_payload
                ) VALUES (?, ?, 'personal', ?, ?, ?, ?, ?, 'heuristic', 'active', '{}')
                ON CONFLICT(indicator_id, methodology_id, scope, owner_user_id) DO UPDATE SET
                    warning_value=excluded.warning_value,
                    danger_value=excluded.danger_value,
                    critical_value=excluded.critical_value,
                    reference_value=excluded.reference_value,
                    created_at=CURRENT_TIMESTAMP
                """,
                (
                    resolved["indicator_id"],
                    resolved["methodology_id"],
                    owner_user_id,
                    *values,
                ),
            )
            return int(
                connection.execute(
                    """
                    SELECT id FROM cr_threshold_sets
                    WHERE indicator_id = ? AND methodology_id = ?
                      AND scope = 'personal' AND owner_user_id = ?
                    """,
                    (
                        resolved["indicator_id"],
                        resolved["methodology_id"],
                        owner_user_id,
                    ),
                ).fetchone()[0]
            )

    def register_scenario(
        self,
        code: str,
        methodology_id: int,
        *,
        name_ru: str,
        name_en: str,
        horizon: str,
        group_codes: tuple[str, ...],
        anchor_groups: tuple[str, ...],
    ) -> int:
        name_payload = json.dumps(
            {"ru": name_ru, "en": name_en}, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        groups_payload = json.dumps(group_codes, separators=(",", ":"))
        anchors_payload = json.dumps(anchor_groups, separators=(",", ":"))
        with self.db.connect() as connection:
            existing = connection.execute(
                """
                SELECT id, name_payload, horizon, group_codes_payload, anchor_groups_payload
                FROM cr_scenario_definitions WHERE code = ? AND methodology_id = ?
                """,
                (code, methodology_id),
            ).fetchone()
            expected = (name_payload, horizon, groups_payload, anchors_payload)
            if existing is not None:
                if tuple(existing[1:]) != expected:
                    raise RuntimeError("scenario changed inside an immutable methodology")
                return int(existing[0])
            cursor = connection.execute(
                """
                INSERT INTO cr_scenario_definitions(
                    code, methodology_id, name_payload, horizon,
                    group_codes_payload, anchor_groups_payload
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (code, methodology_id, *expected),
            )
            return int(cursor.lastrowid)

    def save_observation(self, observation: Observation, *, sync_run_id: int | None = None) -> SavedObservation:
        flags = json.dumps(sorted(flag.value for flag in observation.quality_flags), separators=(",", ":"))
        with self.db.connect() as connection:
            indicator = connection.execute(
                """
                SELECT indicator.id, indicator.unit, source.id
                FROM cr_indicator_definitions AS indicator
                JOIN cr_sources AS source ON source.id = indicator.source_id
                WHERE indicator.code = ? AND source.code = ?
                """,
                (observation.indicator_code, observation.source_code),
            ).fetchone()
            if indicator is None:
                raise ValueError("observation indicator/source pair is not registered")
            if indicator[1] != observation.unit:
                raise ValueError(
                    f"observation unit {observation.unit!r} does not match registered unit {indicator[1]!r}"
                )

            previous = connection.execute(
                """
                SELECT id, value_text FROM cr_observations
                WHERE indicator_id = ? AND source_id = ? AND observed_at = ?
                ORDER BY released_at DESC, fetched_at DESC, id DESC
                LIMIT 1
                """,
                (indicator[0], indicator[2], _utc_text(observation.observed_at)),
            ).fetchone()
            formatted_value = format(observation.value, "f")
            if previous is not None and previous[1] == formatted_value:
                return SavedObservation(int(previous[0]), False, False)
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO cr_observations(
                    indicator_id, source_id, observed_at, released_at, fetched_at,
                    value_text, unit, vintage, quality_flags, content_hash, sync_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    indicator[0],
                    indicator[2],
                    _utc_text(observation.observed_at),
                    _utc_text(observation.released_at),
                    _utc_text(observation.fetched_at),
                    formatted_value,
                    observation.unit,
                    observation.vintage,
                    flags,
                    observation.content_hash,
                    sync_run_id,
                ),
            )
            if cursor.rowcount == 0:
                existing = connection.execute(
                    """
                    SELECT id FROM cr_observations
                    WHERE indicator_id = ? AND source_id = ? AND observed_at = ? AND vintage = ?
                    """,
                    (
                        indicator[0],
                        indicator[2],
                        _utc_text(observation.observed_at),
                        observation.vintage,
                    ),
                ).fetchone()
                return SavedObservation(int(existing[0]), False, False)
            observation_id = int(cursor.lastrowid)
            revision_created = previous is not None and previous[1] != formatted_value
            if revision_created:
                connection.execute(
                    """
                    INSERT INTO cr_observation_revisions(
                        previous_observation_id, revised_observation_id, detected_at
                    ) VALUES (?, ?, ?)
                    """,
                    (previous[0], observation_id, _utc_text(observation.fetched_at)),
                )
            return SavedObservation(observation_id, True, revision_created)

    def save_news_item(
        self, item: NewsItem, *, sync_run_id: int | None = None
    ) -> SavedNewsItem:
        with self.db.connect() as connection:
            source = connection.execute(
                "SELECT id FROM cr_sources WHERE code = ?", (item.source_code,)
            ).fetchone()
            if source is None:
                raise ValueError(f"unknown Crisis Radar source: {item.source_code}")
            existing = connection.execute(
                """
                SELECT id, content_hash FROM cr_news_items
                WHERE source_id = ? AND provider_item_id = ?
                """,
                (source[0], item.provider_item_id),
            ).fetchone()
            values = (
                _utc_text(item.published_at),
                _utc_text(item.fetched_at),
                item.title,
                item.summary,
                item.url,
                item.category,
                item.language,
                item.importance,
                item.content_hash,
                item.publisher,
                item.original_language,
                item.normalized_title,
                item.dedup_hash,
                item.source_tier,
                item.evidence_excerpt,
                item.raw_payload_hash,
                sync_run_id,
            )
            if existing is not None:
                updated = existing["content_hash"] != item.content_hash
                connection.execute(
                    """
                    UPDATE cr_news_items
                    SET published_at=?, fetched_at=?, title=?, summary=?, url=?, category=?,
                        language=?, importance=?, content_hash=?, publisher=?,
                        original_language=?, normalized_title=?, dedup_hash=?, source_tier=?,
                        evidence_excerpt=?, raw_payload_hash=?, sync_run_id=?,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (*values, existing["id"]),
                )
                return SavedNewsItem(int(existing["id"]), False, updated)
            cursor = connection.execute(
                """
                INSERT INTO cr_news_items(
                    source_id, provider_item_id, published_at, fetched_at, title,
                    summary, url, category, language, importance, content_hash,
                    publisher, original_language, normalized_title, dedup_hash, source_tier,
                    evidence_excerpt, raw_payload_hash, sync_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (source[0], item.provider_item_id, *values),
            )
            return SavedNewsItem(int(cursor.lastrowid), True, False)

    def search_evidence_basic(
        self,
        query: str,
        *,
        limit: int = 20,
        published_after: datetime | None = None,
        source_codes: tuple[str, ...] = (),
    ) -> dict:
        normalized = " ".join(query.split())
        if not normalized or len(normalized) > 300 or not 1 <= limit <= 50:
            raise ValueError("invalid evidence search query")
        filters = ["cr_news_fts MATCH ?"]
        parameters: list[object] = [normalized]
        if published_after is not None:
            if published_after.tzinfo is None or published_after.utcoffset() is None:
                raise ValueError("published_after must be timezone-aware")
            filters.append("item.published_at >= ?")
            parameters.append(_utc_text(published_after))
        if source_codes:
            if any(not code.strip() for code in source_codes) or len(source_codes) > 20:
                raise ValueError("invalid evidence source filter")
            filters.append(f"source.code IN ({','.join('?' for _ in source_codes)})")
            parameters.extend(source_codes)
        parameters.append(limit)
        with self.db.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT item.id, source.code AS source_code, item.title, item.summary,
                       item.url, item.published_at, item.original_language,
                       item.source_tier, bm25(cr_news_fts) AS rank
                FROM cr_news_fts
                JOIN cr_news_items AS item ON item.id=cr_news_fts.rowid
                JOIN cr_sources AS source ON source.id=item.source_id
                WHERE {' AND '.join(filters)}
                ORDER BY rank, item.published_at DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return {
            "profile": "basic-local",
            "query": normalized,
            "items": [
                {
                    "evidence_id": int(row["id"]),
                    "source_code": row["source_code"],
                    "title": row["title"],
                    "excerpt": row["summary"] or row["title"],
                    "url": row["url"],
                    "published_at": row["published_at"],
                    "original_language": row["original_language"],
                    "source_tier": row["source_tier"],
                    "lexical_rank": float(row["rank"]),
                }
                for row in rows
            ],
        }

    def save_event_candidate(self, news_item_id: int, candidate: EventCandidate) -> dict:
        if news_item_id < 1:
            raise ValueError("news item id must be positive")
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        with self.db.connect() as connection:
            news = connection.execute(
                """
                SELECT item.published_at, item.source_tier, item.source_id
                FROM cr_news_items AS item WHERE item.id = ?
                """,
                (news_item_id,),
            ).fetchone()
            if news is None:
                raise ValueError("unknown news item for event evidence")
            published_at = datetime.fromisoformat(news["published_at"])
            recent = connection.execute(
                """
                SELECT id, event_key, title, regions_payload, assets_payload
                FROM cr_event_clusters
                WHERE taxonomy = ? AND last_seen_at >= ?
                ORDER BY last_seen_at DESC LIMIT 100
                """,
                (candidate.taxonomy, _utc_text(min(cutoff, published_at - timedelta(days=3)))),
            ).fetchall()
            selected = None
            for row in recent:
                same_context = bool(
                    set(json.loads(row["regions_payload"] or "[]")) & set(candidate.regions)
                    or set(json.loads(row["assets_payload"] or "[]")) & set(candidate.assets)
                )
                if row["event_key"] == candidate.event_key or (
                    same_context and near_duplicate_score(row["title"], candidate.title) >= Decimal("0.65")
                ):
                    selected = row
                    break
            if selected is None:
                cursor = connection.execute(
                    """
                    INSERT INTO cr_event_clusters(
                        event_key, taxonomy, status, title, first_seen_at, last_seen_at,
                        regions_payload, entities_payload, assets_payload, impact_direction,
                        horizon, severity_score_text, confidence_score_text, event_score_text,
                        source_count, official_source_count, rule_version
                    ) VALUES (?, ?, 'discovery', ?, ?, ?, ?, ?, ?, ?, ?, ?, '0', '0', 0, 0, ?)
                    """,
                    (
                        candidate.event_key,
                        candidate.taxonomy,
                        candidate.title,
                        news["published_at"],
                        news["published_at"],
                        json.dumps(candidate.regions, separators=(",", ":")),
                        json.dumps(candidate.entities, separators=(",", ":")),
                        json.dumps(candidate.assets, separators=(",", ":")),
                        candidate.impact_direction,
                        candidate.horizon,
                        format(candidate.severity_score, "f"),
                        EVENT_RULE_VERSION,
                    ),
                )
                event_id = int(cursor.lastrowid)
            else:
                event_id = int(selected["id"])
                connection.execute(
                    """
                    UPDATE cr_event_clusters
                    SET last_seen_at = CASE WHEN last_seen_at < ? THEN ? ELSE last_seen_at END,
                        severity_score_text = CASE
                            WHEN CAST(severity_score_text AS REAL) < ? THEN ? ELSE severity_score_text END,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        news["published_at"],
                        news["published_at"],
                        float(candidate.severity_score),
                        format(candidate.severity_score, "f"),
                        event_id,
                    ),
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO cr_event_evidence(
                    event_id, news_item_id, source_tier, evidence_excerpt, injection_detected
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    news_item_id,
                    candidate.source_tier,
                    candidate.evidence_excerpt,
                    int(candidate.injection_detected),
                ),
            )
            counts = connection.execute(
                """
                SELECT count(DISTINCT item.source_id) AS source_count,
                       count(DISTINCT CASE WHEN evidence.source_tier='A' THEN item.source_id END)
                           AS official_count,
                       max(CAST(cluster.severity_score_text AS REAL)) AS severity
                FROM cr_event_evidence AS evidence
                JOIN cr_news_items AS item ON item.id=evidence.news_item_id
                JOIN cr_event_clusters AS cluster ON cluster.id=evidence.event_id
                WHERE evidence.event_id=?
                """,
                (event_id,),
            ).fetchone()
            source_count = int(counts["source_count"])
            official_count = int(counts["official_count"])
            status = (
                "official"
                if official_count >= 1
                else "corroborated"
                if source_count >= 2
                else "discovery"
                if candidate.source_tier == "C"
                else "watch"
            )
            confidence = min(
                Decimal("1"),
                Decimal("0.25") + Decimal("0.25") * source_count + Decimal("0.25") * official_count,
            )
            score = event_score(
                severity=Decimal(str(counts["severity"])),
                source_tier="A" if official_count else candidate.source_tier,
                source_count=source_count,
                official_source_count=official_count,
            )
            connection.execute(
                """
                UPDATE cr_event_clusters
                SET status=?, source_count=?, official_source_count=?,
                    confidence_score_text=?, event_score_text=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (status, source_count, official_count, format(confidence, "f"), format(score, "f"), event_id),
            )
            return {"event_id": event_id, "status": status, "source_count": source_count}

    def save_news_evidence(
        self,
        news_item_id: int,
        evidence: NewsEvidence,
        *,
        methodology_code: str,
        methodology_version: str,
        rule_version: str,
    ) -> bool:
        explanation = json.dumps(
            {"ru": evidence.explanation_ru, "en": evidence.explanation_en},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        rules = json.dumps(evidence.rule_codes, separators=(",", ":"))
        with self.db.connect() as connection:
            scenario = connection.execute(
                """
                SELECT scenario.id, methodology.id AS methodology_id
                FROM cr_scenario_definitions AS scenario
                JOIN cr_methodology_versions AS methodology
                    ON methodology.id = scenario.methodology_id
                WHERE scenario.code = ? AND methodology.code = ? AND methodology.version = ?
                """,
                (evidence.scenario_code, methodology_code, methodology_version),
            ).fetchone()
            if scenario is None:
                raise ValueError(f"unknown scenario for news evidence: {evidence.scenario_code}")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO cr_news_evidence(
                    news_item_id, scenario_id, methodology_id, relevance_score_text,
                    severity, rule_version, rule_codes_payload, explanation_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    news_item_id,
                    scenario["id"],
                    scenario["methodology_id"],
                    format(evidence.relevance_score, "f"),
                    evidence.severity,
                    rule_version,
                    rules,
                    explanation,
                ),
            )
            return cursor.rowcount == 1

    def news_payload(
        self,
        *,
        methodology_code: str,
        methodology_version: str,
        locale: str = "ru",
        days: int = 14,
        limit: int = 20,
        as_of: datetime | None = None,
    ) -> dict:
        if locale not in {"ru", "en"}:
            raise ValueError("locale must be ru or en")
        if days < 1 or days > 90:
            raise ValueError("news days must be between 1 and 90")
        if limit < 1 or limit > 50:
            raise ValueError("news limit must be between 1 and 50")
        now = as_of or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT item.id, item.published_at, item.fetched_at, item.title,
                       item.summary, item.url, item.category, item.language,
                       item.importance, source.code AS source_code,
                       source.name AS source_name, scenario.code AS scenario_code,
                       scenario.name_payload, evidence.relevance_score_text,
                       evidence.severity, evidence.rule_codes_payload,
                       evidence.explanation_payload
                FROM cr_news_evidence AS evidence
                JOIN cr_news_items AS item ON item.id = evidence.news_item_id
                JOIN cr_sources AS source ON source.id = item.source_id
                JOIN cr_scenario_definitions AS scenario ON scenario.id = evidence.scenario_id
                JOIN cr_methodology_versions AS methodology
                    ON methodology.id = evidence.methodology_id
                WHERE methodology.code = ? AND methodology.version = ?
                  AND item.published_at >= ? AND item.published_at <= ?
                ORDER BY item.published_at DESC,
                         CAST(evidence.relevance_score_text AS REAL) DESC,
                         item.id DESC
                """,
                (
                    methodology_code,
                    methodology_version,
                    _utc_text(cutoff),
                    _utc_text(now),
                ),
            ).fetchall()
        grouped: dict[int, dict] = {}
        for row in rows:
            news_item = grouped.setdefault(
                int(row["id"]),
                {
                    "id": int(row["id"]),
                    "published_at": row["published_at"],
                    "fetched_at": row["fetched_at"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "url": row["url"],
                    "category": row["category"],
                    "language": row["language"],
                    "importance": row["importance"],
                    "source": {"code": row["source_code"], "name": row["source_name"]},
                    "scenarios": [],
                },
            )
            names = json.loads(row["name_payload"] or "{}")
            explanations = json.loads(row["explanation_payload"] or "{}")
            news_item["scenarios"].append(
                {
                    "code": row["scenario_code"],
                    "name": names.get(locale, row["scenario_code"]),
                    "relevance": row["relevance_score_text"],
                    "severity": row["severity"],
                    "rules": json.loads(row["rule_codes_payload"] or "[]"),
                    "explanation": explanations.get(locale, ""),
                }
            )
        items = list(grouped.values())[:limit]
        return {
            "ready": bool(items),
            "as_of": _utc_text(now),
            "window_days": days,
            "items": items,
        }

    def events_payload(
        self,
        *,
        days: int = 14,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> dict:
        if days < 1 or days > 90 or limit < 1 or limit > 100:
            raise ValueError("invalid event query bounds")
        now = as_of or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT cluster.*, evidence.id AS evidence_id, evidence.source_tier,
                       evidence.evidence_excerpt, evidence.injection_detected,
                       item.id AS news_item_id, item.published_at, item.title AS evidence_title,
                       item.url, item.publisher, item.original_language,
                       source.code AS source_code
                FROM cr_event_clusters AS cluster
                JOIN cr_event_evidence AS evidence ON evidence.event_id=cluster.id
                JOIN cr_news_items AS item ON item.id=evidence.news_item_id
                JOIN cr_sources AS source ON source.id=item.source_id
                WHERE cluster.last_seen_at >= ? AND cluster.last_seen_at <= ?
                ORDER BY CAST(cluster.event_score_text AS REAL) DESC,
                         cluster.last_seen_at DESC, cluster.id DESC, evidence.id
                """,
                (_utc_text(cutoff), _utc_text(now)),
            ).fetchall()
        grouped: dict[int, dict] = {}
        for row in rows:
            event = grouped.setdefault(
                int(row["id"]),
                {
                    "id": int(row["id"]),
                    "taxonomy": row["taxonomy"],
                    "status": row["status"],
                    "title": row["title"],
                    "first_seen_at": row["first_seen_at"],
                    "last_seen_at": row["last_seen_at"],
                    "regions": json.loads(row["regions_payload"] or "[]"),
                    "entities": json.loads(row["entities_payload"] or "[]"),
                    "assets": json.loads(row["assets_payload"] or "[]"),
                    "impact_direction": row["impact_direction"],
                    "horizon": row["horizon"],
                    "severity": row["severity_score_text"],
                    "confidence": row["confidence_score_text"],
                    "event_score": row["event_score_text"],
                    "raw_event_score": row["event_score_text"],
                    "source_count": int(row["source_count"]),
                    "official_source_count": int(row["official_source_count"]),
                    "rule_version": row["rule_version"],
                    "evidence": [],
                },
            )
            event["evidence"].append(
                {
                    "id": int(row["evidence_id"]),
                    "news_item_id": int(row["news_item_id"]),
                    "source_code": row["source_code"],
                    "source_tier": row["source_tier"],
                    "publisher": row["publisher"],
                    "published_at": row["published_at"],
                    "title": row["evidence_title"],
                    "excerpt": row["evidence_excerpt"],
                    "url": row["url"],
                    "original_language": row["original_language"],
                    "injection_detected": bool(row["injection_detected"]),
                }
            )
        half_lives = {
            "bankruptcy": Decimal("720"),
            "default": Decimal("1440"),
            "bank_run": Decimal("72"),
            "emergency_liquidity": Decimal("72"),
            "sanctions": Decimal("504"),
            "armed_conflict": Decimal("336"),
            "supply_disruption": Decimal("240"),
            "commodity_shock": Decimal("336"),
            "regulatory_restriction": Decimal("504"),
            "cyber_exchange_failure": Decimal("48"),
            "stablecoin_failure": Decimal("48"),
            "recession_signal": Decimal("1440"),
        }
        tier_rank = {"A": 0, "B": 1, "C": 2}
        for event in grouped.values():
            age_hours = Decimal(
                str(max(0, (now - datetime.fromisoformat(event["last_seen_at"])).total_seconds() / 3600))
            )
            best_tier = min(
                (item["source_tier"] for item in event["evidence"]),
                key=lambda value: tier_rank[value],
            )
            half_life = half_lives.get(event["taxonomy"], Decimal("168"))
            event["event_score"] = format(
                event_score(
                    severity=Decimal(event["severity"]),
                    source_tier=best_tier,
                    source_count=event["source_count"],
                    official_source_count=event["official_source_count"],
                    age_hours=age_hours,
                    half_life_hours=half_life,
                ),
                "f",
            )
            event["age_hours"] = format(age_hours.quantize(Decimal(".01")), "f")
            event["half_life_hours"] = format(half_life, "f")
        items = sorted(
            grouped.values(),
            key=lambda item: (Decimal(item["event_score"]), item["last_seen_at"]),
            reverse=True,
        )[:limit]
        return {"ready": bool(items), "as_of": _utc_text(now), "items": items}

    def save_news_coverage_snapshot(
        self,
        *,
        methodology_id: int,
        snapshot_at: datetime,
    ) -> dict:
        health = self.source_health_payload(as_of=snapshot_at)
        required = [
            item for item in health["sources"]
            if item["enabled"] and item["access_type"] in {"rss", "official_api"}
        ]
        healthy = sum(item["status"] == "healthy" for item in required)
        degraded = sum(item["status"] in {"degraded", "stale"} for item in required)
        failed = len(required) - healthy - degraded
        ratio = (
            Decimal("0")
            if not required
            else (Decimal(healthy) + Decimal(".5") * Decimal(degraded)) / Decimal(len(required))
        ).quantize(Decimal(".0001"))
        status = "healthy" if ratio >= Decimal(".85") else "degraded" if ratio >= Decimal(".70") else "insufficient_data"
        region_by_source = {
            "fed_news": "US", "sec_news": "US", "cftc_news": "US",
            "ecb_news": "EU", "boj_news": "JPN", "rbi_news": "IND",
            "bis_news": "GLOBAL",
            "boe_news": "GBR", "boc_news": "CAN",
            "fdic_news": "US",
            "hkma_news": "HKG",
            "ofac_news": "GLOBAL",
        }
        covered_regions = {
            region_by_source[item["code"]]
            for item in required
            if item["status"] in {"healthy", "degraded", "stale"}
            and item["code"] in region_by_source
        }
        expected_regions = set(region_by_source.values())
        missing_regions = sorted(expected_regions - covered_regions)
        payload = {
            "status": status,
            "ratio": format(ratio, "f"),
            "expected_source_count": len(required),
            "healthy_source_count": healthy,
            "degraded_source_count": degraded,
            "failed_source_count": failed,
            "missing_regions": missing_regions,
            "sources": [
                {"code": item["code"], "status": item["status"]}
                for item in required
            ],
        }
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO cr_news_coverage_snapshots(
                    methodology_id, snapshot_at, status, ratio_text,
                    expected_source_count, healthy_source_count,
                    degraded_source_count, failed_source_count,
                    missing_regions_payload, source_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(methodology_id, snapshot_at) DO UPDATE SET
                    status=excluded.status,
                    ratio_text=excluded.ratio_text,
                    expected_source_count=excluded.expected_source_count,
                    healthy_source_count=excluded.healthy_source_count,
                    degraded_source_count=excluded.degraded_source_count,
                    failed_source_count=excluded.failed_source_count,
                    missing_regions_payload=excluded.missing_regions_payload,
                    source_payload=excluded.source_payload
                """,
                (
                    methodology_id, _utc_text(snapshot_at), status, format(ratio, "f"),
                    len(required), healthy, degraded, failed,
                    json.dumps(missing_regions, separators=(",", ":")),
                    json.dumps(payload["sources"], separators=(",", ":")),
                ),
            )
        return payload

    def latest_analysis_inputs(self, methodology_code: str, methodology_version: str) -> list[AnalysisInput]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    methodology.id AS methodology_id,
                    observation.id AS observation_id,
                    indicator.code AS indicator_code,
                    indicator.group_code,
                    indicator.unit,
                    indicator.risk_direction,
                    indicator.max_staleness_seconds,
                    indicator.frequency,
                    source.code AS source_code,
                    thresholds.warning_value,
                    thresholds.danger_value,
                    thresholds.critical_value,
                    thresholds.reference_value,
                    observation.value_text,
                    observation.observed_at,
                    observation.released_at,
                    observation.fetched_at,
                    observation.vintage,
                    observation.quality_flags,
                    observation.content_hash
                FROM cr_indicator_definitions AS indicator
                JOIN cr_sources AS source ON source.id = indicator.source_id
                JOIN cr_threshold_sets AS thresholds
                    ON thresholds.indicator_id = indicator.id
                    AND thresholds.scope = 'system'
                    AND thresholds.owner_user_id = 0
                JOIN cr_methodology_versions AS methodology
                    ON methodology.id = thresholds.methodology_id
                JOIN cr_observations AS observation ON observation.id = (
                    SELECT latest.id
                    FROM cr_observations AS latest
                    WHERE latest.indicator_id = indicator.id
                    ORDER BY latest.observed_at DESC, latest.released_at DESC,
                             latest.fetched_at DESC, latest.id DESC
                    LIMIT 1
                )
                WHERE methodology.code = ? AND methodology.version = ? AND indicator.enabled = 1
                ORDER BY indicator.code
                """,
                (methodology_code, methodology_version),
            ).fetchall()
        return _analysis_inputs_from_rows(rows)

    def analysis_inputs_as_of(
        self,
        methodology_code: str,
        methodology_version: str,
        *,
        as_of: datetime,
    ) -> list[AnalysisInput]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        as_of_text = _utc_text(as_of)
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    methodology.id AS methodology_id,
                    observation.id AS observation_id,
                    indicator.code AS indicator_code,
                    indicator.group_code,
                    indicator.unit,
                    indicator.risk_direction,
                    indicator.max_staleness_seconds,
                    indicator.frequency,
                    source.code AS source_code,
                    thresholds.warning_value,
                    thresholds.danger_value,
                    thresholds.critical_value,
                    thresholds.reference_value,
                    observation.value_text,
                    observation.observed_at,
                    observation.released_at,
                    observation.fetched_at,
                    observation.vintage,
                    observation.quality_flags,
                    observation.content_hash
                FROM cr_indicator_definitions AS indicator
                JOIN cr_sources AS source ON source.id = indicator.source_id
                JOIN cr_threshold_sets AS thresholds
                    ON thresholds.indicator_id = indicator.id
                    AND thresholds.scope = 'system'
                    AND thresholds.owner_user_id = 0
                JOIN cr_methodology_versions AS methodology
                    ON methodology.id = thresholds.methodology_id
                JOIN cr_observations AS observation ON observation.id = (
                    SELECT latest.id
                    FROM cr_observations AS latest
                    WHERE latest.indicator_id = indicator.id
                      AND latest.observed_at <= ?
                      AND latest.released_at <= ?
                    ORDER BY latest.observed_at DESC, latest.released_at DESC,
                             latest.fetched_at DESC, latest.id DESC
                    LIMIT 1
                )
                WHERE methodology.code = ? AND methodology.version = ? AND indicator.enabled = 1
                ORDER BY indicator.code
                """,
                (as_of_text, as_of_text, methodology_code, methodology_version),
            ).fetchall()
        return _analysis_inputs_from_rows(rows)

    def recent_indicator_values(self, code: str, *, limit: int = 5) -> list[Decimal]:
        if limit < 1 or limit > 20:
            raise ValueError("recent indicator value limit must be between 1 and 20")
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT observation.value_text
                FROM cr_observations AS observation
                JOIN cr_indicator_definitions AS indicator
                    ON indicator.id = observation.indicator_id
                WHERE indicator.code = ?
                  AND observation.id = (
                    SELECT latest.id
                    FROM cr_observations AS latest
                    WHERE latest.indicator_id = observation.indicator_id
                      AND latest.observed_at = observation.observed_at
                    ORDER BY latest.released_at DESC, latest.fetched_at DESC, latest.id DESC
                    LIMIT 1
                  )
                ORDER BY observation.observed_at DESC, observation.id DESC
                LIMIT ?
                """,
                (code, limit),
            ).fetchall()
        return [Decimal(row["value_text"]) for row in rows]

    def recent_indicator_values_as_of(
        self, code: str, *, as_of: datetime, limit: int = 5
    ) -> list[Decimal]:
        if limit < 1 or limit > 20:
            raise ValueError("recent indicator value limit must be between 1 and 20")
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        as_of_text = _utc_text(as_of)
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT observation.value_text
                FROM cr_observations AS observation
                JOIN cr_indicator_definitions AS indicator
                    ON indicator.id = observation.indicator_id
                WHERE indicator.code = ?
                  AND observation.observed_at <= ?
                  AND observation.released_at <= ?
                  AND observation.id = (
                    SELECT latest.id
                    FROM cr_observations AS latest
                    WHERE latest.indicator_id = observation.indicator_id
                      AND latest.observed_at = observation.observed_at
                      AND latest.released_at <= ?
                    ORDER BY latest.released_at DESC, latest.fetched_at DESC, latest.id DESC
                    LIMIT 1
                  )
                ORDER BY observation.observed_at DESC, observation.id DESC
                LIMIT ?
                """,
                (code, as_of_text, as_of_text, as_of_text, limit),
            ).fetchall()
        return [Decimal(row["value_text"]) for row in rows]

    def indicator_points_as_of(
        self,
        code: str,
        *,
        as_of: datetime,
        limit: int = 5000,
        exclude_retrospective_revised: bool = False,
    ) -> tuple[TimePoint, ...]:
        if limit < 2 or limit > 20000:
            raise ValueError("trend point limit must be between 2 and 20000")
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        as_of_text = _utc_text(as_of)
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT observation.observed_at, observation.released_at, observation.value_text
                FROM cr_observations AS observation
                JOIN cr_indicator_definitions AS indicator ON indicator.id=observation.indicator_id
                WHERE indicator.code=? AND observation.observed_at <= ? AND observation.released_at <= ?
                  AND (? = 0 OR observation.quality_flags NOT LIKE '%"retrospective_revised"%')
                  AND observation.id=(
                    SELECT latest.id FROM cr_observations AS latest
                    WHERE latest.indicator_id=observation.indicator_id
                      AND latest.observed_at=observation.observed_at
                      AND latest.released_at <= ?
                      AND (? = 0 OR latest.quality_flags NOT LIKE '%"retrospective_revised"%')
                    ORDER BY latest.released_at DESC, latest.fetched_at DESC, latest.id DESC LIMIT 1
                  )
                ORDER BY observation.observed_at DESC, observation.id DESC LIMIT ?
                """,
                (
                    code,
                    as_of_text,
                    as_of_text,
                    int(exclude_retrospective_revised),
                    as_of_text,
                    int(exclude_retrospective_revised),
                    limit,
                ),
            ).fetchall()
        return tuple(
            reversed(
                tuple(
                    TimePoint(
                        observed_at=datetime.fromisoformat(row["observed_at"]),
                        released_at=datetime.fromisoformat(row["released_at"]),
                        value=Decimal(row["value_text"]),
                    )
                    for row in rows
                )
            )
        )

    def save_trend_features(
        self,
        features: tuple[IndicatorFeatures, ...],
        contagion: ContagionFeatures,
        *,
        methodology_id: int,
    ) -> None:
        snapshot_text = _utc_text(contagion.snapshot_at)
        with self.db.connect() as connection:
            for item in features:
                indicator = connection.execute(
                    "SELECT id FROM cr_indicator_definitions WHERE code=?", (item.indicator_code,)
                ).fetchone()
                if indicator is None:
                    raise ValueError(f"unknown trend indicator: {item.indicator_code}")
                payload = json.dumps(
                    feature_payload(item),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                lineage = json.dumps(
                    {
                        "feature_version": FEATURE_VERSION,
                        "input_checksum": item.input_checksum,
                        "causal_cutoff": snapshot_text,
                        "future_observations_allowed": False,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                connection.execute(
                    """
                    INSERT INTO cr_indicator_features(
                        indicator_id, methodology_id, snapshot_at, feature_version,
                        features_payload, lineage_payload, input_checksum
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(indicator_id, methodology_id, snapshot_at, feature_version)
                    DO UPDATE SET features_payload=excluded.features_payload,
                                  lineage_payload=excluded.lineage_payload,
                                  input_checksum=excluded.input_checksum
                    """,
                    (
                        indicator["id"], methodology_id, snapshot_text, FEATURE_VERSION,
                        payload, lineage, item.input_checksum,
                    ),
                )
            contagion_payload = json.dumps(
                {
                    "snapshot_at": snapshot_text,
                    "breadth": str(contagion.breadth),
                    "stressed_count": contagion.stressed_count,
                    "indicator_count": contagion.indicator_count,
                    "mean_absolute_correlation": (
                        None
                        if contagion.mean_absolute_correlation is None
                        else str(contagion.mean_absolute_correlation)
                    ),
                    "stress_correlation_regime": contagion.stress_correlation_regime,
                    "lead_lag_edges": contagion.lead_lag_edges,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                """
                INSERT INTO cr_contagion_features(
                    methodology_id, snapshot_at, feature_version, breadth_score_text,
                    stress_correlation_text, payload, input_checksum
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(methodology_id, snapshot_at, feature_version)
                DO UPDATE SET breadth_score_text=excluded.breadth_score_text,
                              stress_correlation_text=excluded.stress_correlation_text,
                              payload=excluded.payload, input_checksum=excluded.input_checksum
                """,
                (
                    methodology_id, snapshot_text, FEATURE_VERSION, str(contagion.breadth),
                    None
                    if contagion.mean_absolute_correlation is None
                    else str(contagion.mean_absolute_correlation),
                    contagion_payload, contagion.input_checksum,
                ),
            )

    def latest_trend_payload(self, *, methodology_id: int) -> dict:
        with self.db.connect() as connection:
            contagion = connection.execute(
                """
                SELECT snapshot_at, payload, input_checksum FROM cr_contagion_features
                WHERE methodology_id=? AND feature_version=?
                ORDER BY snapshot_at DESC, id DESC LIMIT 1
                """,
                (methodology_id, FEATURE_VERSION),
            ).fetchone()
            if contagion is None:
                return {"ready": False, "feature_version": FEATURE_VERSION, "indicators": []}
            rows = connection.execute(
                """
                SELECT indicator.code, feature.features_payload, feature.lineage_payload
                FROM cr_indicator_features AS feature
                JOIN cr_indicator_definitions AS indicator ON indicator.id=feature.indicator_id
                WHERE feature.methodology_id=? AND feature.feature_version=?
                  AND feature.snapshot_at=? ORDER BY indicator.code
                """,
                (methodology_id, FEATURE_VERSION, contagion["snapshot_at"]),
            ).fetchall()
        return {
            "ready": True,
            "feature_version": FEATURE_VERSION,
            "snapshot_at": contagion["snapshot_at"],
            "input_checksum": contagion["input_checksum"],
            "contagion": json.loads(contagion["payload"]),
            "indicators": [
                {
                    "code": row["code"],
                    "features": json.loads(row["features_payload"]),
                    "lineage": json.loads(row["lineage_payload"]),
                }
                for row in rows
            ],
        }

    def save_scenario_fusion_states(
        self,
        states: tuple[ScenarioFusionState, ...],
        *,
        methodology_id: int,
    ) -> None:
        with self.db.connect() as connection:
            for state in states:
                scenario = connection.execute(
                    """
                    SELECT id FROM cr_scenario_definitions
                    WHERE code=? AND methodology_id=? AND enabled=1
                    """,
                    (state.code, methodology_id),
                ).fetchone()
                if scenario is None:
                    raise LookupError(f"fusion scenario not registered: {state.code}")
                payload = fusion_payload(state)
                components = json.dumps(
                    payload["components"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                explanation = json.dumps(
                    payload["explanation"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                connection.execute(
                    """
                    INSERT INTO cr_scenario_fusion_states(
                        scenario_id, methodology_id, snapshot_at, fusion_version, status,
                        strength_score_text, reliability_score_text,
                        independent_cluster_count, anchor_active, components_payload,
                        explanation_payload, input_checksum
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scenario_id, methodology_id, snapshot_at, fusion_version)
                    DO UPDATE SET status=excluded.status,
                                  strength_score_text=excluded.strength_score_text,
                                  reliability_score_text=excluded.reliability_score_text,
                                  independent_cluster_count=excluded.independent_cluster_count,
                                  anchor_active=excluded.anchor_active,
                                  components_payload=excluded.components_payload,
                                  explanation_payload=excluded.explanation_payload,
                                  input_checksum=excluded.input_checksum
                    """,
                    (
                        scenario["id"],
                        methodology_id,
                        _utc_text(state.snapshot_at),
                        FUSION_VERSION,
                        state.status.value,
                        str(state.strength),
                        str(state.reliability),
                        state.independent_numeric_clusters,
                        int(state.anchor_active),
                        components,
                        explanation,
                        state.input_checksum,
                    ),
                )

    def latest_scenario_fusion_payload(self, *, methodology_id: int, locale: str = "ru") -> dict:
        if locale not in {"ru", "en"}:
            raise ValueError("locale must be ru or en")
        with self.db.connect() as connection:
            latest = connection.execute(
                """
                SELECT MAX(snapshot_at) AS snapshot_at FROM cr_scenario_fusion_states
                WHERE methodology_id=? AND fusion_version=?
                """,
                (methodology_id, FUSION_VERSION),
            ).fetchone()
            if latest is None or latest["snapshot_at"] is None:
                return {"ready": False, "fusion_version": FUSION_VERSION, "items": []}
            rows = connection.execute(
                """
                SELECT definition.code, definition.name_payload, definition.horizon,
                       fusion.status, fusion.strength_score_text,
                       fusion.reliability_score_text, fusion.independent_cluster_count,
                       fusion.anchor_active, fusion.components_payload,
                       fusion.explanation_payload, fusion.input_checksum
                FROM cr_scenario_fusion_states AS fusion
                JOIN cr_scenario_definitions AS definition ON definition.id=fusion.scenario_id
                WHERE fusion.methodology_id=? AND fusion.fusion_version=?
                  AND fusion.snapshot_at=?
                ORDER BY CAST(fusion.strength_score_text AS REAL) DESC, definition.code
                """,
                (methodology_id, FUSION_VERSION, latest["snapshot_at"]),
            ).fetchall()
        return {
            "ready": True,
            "fusion_version": FUSION_VERSION,
            "snapshot_at": latest["snapshot_at"],
            "items": [
                {
                    "code": row["code"],
                    "name": json.loads(row["name_payload"] or "{}").get(locale, row["code"]),
                    "horizon": row["horizon"],
                    "status": row["status"],
                    "strength": row["strength_score_text"],
                    "reliability": row["reliability_score_text"],
                    "independent_numeric_clusters": int(row["independent_cluster_count"]),
                    "anchor_active": bool(row["anchor_active"]),
                    "components": json.loads(row["components_payload"] or "{}"),
                    "explanation": json.loads(row["explanation_payload"] or "{}").get(locale, ""),
                    "input_checksum": row["input_checksum"],
                    "historical_probability": None,
                }
                for row in rows
            ],
        }

    def operational_metrics_payload(self, *, as_of: datetime | None = None) -> dict:
        now = as_of or datetime.now(timezone.utc)
        cutoff = _utc_text(now - timedelta(hours=24))
        cutoff_sql = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db.connect() as connection:
            snapshot = connection.execute(
                """
                SELECT snapshot_at, stage, coverage_status, coverage_ratio_text
                FROM cr_market_snapshots ORDER BY snapshot_at DESC, id DESC LIMIT 1
                """
            ).fetchone()
            sync_rows = connection.execute(
                """
                SELECT status, count(*) AS count FROM cr_sync_runs
                WHERE started_at >= ? GROUP BY status
                """,
                (cutoff,),
            ).fetchall()
            queues = {
                "scenario_alerts": connection.execute(
                    "SELECT count(*) FROM cr_alert_deliveries WHERE status IN ('pending','failed')"
                ).fetchone()[0],
                "data_health_alerts": connection.execute(
                    "SELECT count(*) FROM cr_data_health_deliveries WHERE status IN ('pending','failed')"
                ).fetchone()[0],
                "reports": connection.execute(
                    "SELECT count(*) FROM cr_report_deliveries WHERE status IN ('pending','failed')"
                ).fetchone()[0],
            }
            events = connection.execute(
                "SELECT count(*) FROM cr_event_clusters WHERE last_seen_at >= ?", (cutoff,)
            ).fetchone()[0]
            agent = connection.execute(
                """
                SELECT count(*) AS total, sum(CASE WHEN grounded=0 THEN 1 ELSE 0 END) AS rejected
                FROM cr_agent_messages WHERE role='assistant' AND created_at >= ?
                """,
                (cutoff_sql,),
            ).fetchone()
        snapshot_at = None if snapshot is None else datetime.fromisoformat(snapshot["snapshot_at"])
        return {
            "as_of": _utc_text(now),
            "snapshot": None
            if snapshot is None
            else {
                "as_of": snapshot["snapshot_at"],
                "lag_seconds": max(0, int((now - snapshot_at).total_seconds())),
                "stage": snapshot["stage"],
                "coverage_status": snapshot["coverage_status"],
                "coverage_ratio": snapshot["coverage_ratio_text"],
            },
            "sync_runs_24h": {row["status"]: int(row["count"]) for row in sync_rows},
            "queue_depth": {key: int(value) for key, value in queues.items()},
            "events_24h": int(events),
            "agent_24h": {
                "responses": int(agent["total"] or 0),
                "grounding_rejections": int(agent["rejected"] or 0),
            },
        }

    def save_backtest_result(
        self,
        result: BacktestResult,
        *,
        methodology_code: str,
        methodology_version: str,
        parameters: dict,
        completed_at: datetime | None = None,
    ) -> int:
        now = completed_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("completed_at must be timezone-aware")
        metrics_payload = _backtest_metrics_payload(result)
        with self.db.connect() as connection:
            methodology = connection.execute(
                """
                SELECT id FROM cr_methodology_versions
                WHERE code = ? AND version = ?
                """,
                (methodology_code, methodology_version),
            ).fetchone()
            if methodology is None:
                raise LookupError("backtest methodology not found")
            scenario = connection.execute(
                """
                SELECT id FROM cr_scenario_definitions
                WHERE code = ? AND methodology_id = ? AND enabled = 1
                """,
                (result.scenario_code, methodology["id"]),
            ).fetchone()
            if scenario is None:
                raise LookupError("backtest scenario not found")
            run_id = connection.execute(
                """
                INSERT INTO cr_backtest_runs(
                    methodology_id, scenario_code, horizon_seconds, started_at,
                    completed_at, status, parameters_payload, metrics_payload,
                    sample_count, scored_count
                ) VALUES (?, ?, ?, ?, ?, 'succeeded', ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    methodology["id"],
                    result.scenario_code,
                    round(result.horizon.total_seconds()),
                    _utc_text(now),
                    _utc_text(now),
                    json.dumps(parameters, ensure_ascii=False, sort_keys=True),
                    json.dumps(metrics_payload, ensure_ascii=False, sort_keys=True),
                    result.metrics.sample_count,
                    result.metrics.scored_count,
                ),
            ).fetchone()[0]
            connection.executemany(
                """
                INSERT INTO cr_backtest_predictions(
                    run_id, predicted_at, horizon_end, event_at, signal_score_text,
                    outcome, calibrated_probability_text, baseline_probability_text,
                    confidence, training_sample_count, calibration_bin,
                    latest_training_horizon_end
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        _utc_text(item.sample.predicted_at),
                        _utc_text(item.sample.horizon_end),
                        None if item.sample.event_at is None else _utc_text(item.sample.event_at),
                        _decimal_text(item.sample.signal_score),
                        int(item.sample.outcome),
                        _decimal_text(item.calibrated_probability),
                        _decimal_text(item.baseline_probability),
                        item.confidence,
                        item.training_sample_count,
                        item.calibration_bin,
                        None
                        if item.latest_training_horizon_end is None
                        else _utc_text(item.latest_training_horizon_end),
                    )
                    for item in result.predictions
                ],
            )
            connection.executemany(
                """
                INSERT INTO cr_calibration_bins(
                    run_id, bin_index, lower_text, upper_text, prediction_count,
                    average_probability_text, observed_rate_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        item.index,
                        _decimal_text(item.lower),
                        _decimal_text(item.upper),
                        item.prediction_count,
                        _decimal_text(item.average_probability),
                        _decimal_text(item.observed_rate),
                    )
                    for item in result.calibration_bins
                ],
            )
        return int(run_id)

    def register_event_catalog(self, catalog: EventCatalogVersion) -> int:
        definition_payload = json.dumps(
            catalog.definition, ensure_ascii=False, sort_keys=True
        )
        limitations_payload = json.dumps(
            catalog.limitations, ensure_ascii=False
        )
        with self.db.connect() as connection:
            existing = connection.execute(
                """
                SELECT id, checksum FROM cr_event_catalog_versions
                WHERE scenario_code = ? AND version = ?
                """,
                (catalog.scenario_code, catalog.version),
            ).fetchone()
            if existing is not None:
                if existing["checksum"] != catalog.checksum:
                    raise RuntimeError("event catalog checksum mismatch")
                return int(existing["id"])
            catalog_id = connection.execute(
                """
                INSERT INTO cr_event_catalog_versions(
                    scenario_code, version, checksum, source_name, source_url,
                    definition_payload, limitations_payload, effective_from
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    catalog.scenario_code,
                    catalog.version,
                    catalog.checksum,
                    catalog.source_name,
                    catalog.source_url,
                    definition_payload,
                    limitations_payload,
                    _utc_text(catalog.effective_from),
                ),
            ).fetchone()[0]
            connection.executemany(
                """
                INSERT INTO cr_event_labels(
                    catalog_id, event_code, started_at, ended_at, start_precision,
                    end_precision, region_code, source_url, source_note, label_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        catalog_id,
                        item.code,
                        _utc_text(item.started_at),
                        None if item.ended_at is None else _utc_text(item.ended_at),
                        item.start_precision,
                        item.end_precision,
                        item.region_code,
                        item.source_url,
                        item.source_note,
                        item.status,
                    )
                    for item in catalog.labels
                ],
            )
        return int(catalog_id)

    def event_catalog_payload(
        self, scenario_code: str, *, version: str | None = None
    ) -> dict | None:
        with self.db.connect() as connection:
            if version is None:
                catalog = connection.execute(
                    """
                    SELECT * FROM cr_event_catalog_versions
                    WHERE scenario_code = ? AND status = 'active'
                    ORDER BY id DESC LIMIT 1
                    """,
                    (scenario_code,),
                ).fetchone()
            else:
                catalog = connection.execute(
                    """
                    SELECT * FROM cr_event_catalog_versions
                    WHERE scenario_code = ? AND version = ?
                    """,
                    (scenario_code, version),
                ).fetchone()
            if catalog is None:
                return None
            labels = connection.execute(
                """
                SELECT event_code, started_at, ended_at, start_precision,
                       end_precision, region_code, source_url, source_note, label_status
                FROM cr_event_labels WHERE catalog_id = ?
                ORDER BY started_at, event_code
                """,
                (catalog["id"],),
            ).fetchall()
        return {
            "catalog_id": int(catalog["id"]),
            "scenario_code": catalog["scenario_code"],
            "version": catalog["version"],
            "checksum": catalog["checksum"],
            "status": catalog["status"],
            "source_name": catalog["source_name"],
            "source_url": catalog["source_url"],
            "definition": json.loads(catalog["definition_payload"] or "{}"),
            "limitations": json.loads(catalog["limitations_payload"] or "[]"),
            "effective_from": catalog["effective_from"],
            "labels": [dict(item) for item in labels],
        }

    def event_catalog_events(self, catalog_id: int) -> tuple[ScenarioEvent, ...]:
        with self.db.connect() as connection:
            catalog = connection.execute(
                "SELECT scenario_code FROM cr_event_catalog_versions WHERE id = ?",
                (catalog_id,),
            ).fetchone()
            if catalog is None:
                raise LookupError("event catalog not found")
            rows = connection.execute(
                """
                SELECT started_at, ended_at FROM cr_event_labels
                WHERE catalog_id = ? ORDER BY started_at, id
                """,
                (catalog_id,),
            ).fetchall()
        return tuple(
            ScenarioEvent(
                scenario_code=catalog["scenario_code"],
                started_at=datetime.fromisoformat(row["started_at"]),
                ended_at=(
                    None if row["ended_at"] is None else datetime.fromisoformat(row["ended_at"])
                ),
            )
            for row in rows
        )

    def save_replay_result(self, result) -> int:
        with self.db.connect() as connection:
            methodology = connection.execute(
                """
                SELECT id FROM cr_methodology_versions
                WHERE code = ? AND version = ?
                """,
                (result.methodology_code, result.methodology_version),
            ).fetchone()
            if methodology is None:
                raise LookupError("replay methodology not found")
            run_id = connection.execute(
                """
                INSERT INTO cr_replay_runs(
                    methodology_id, scenario_code, engine_version, started_at,
                    ended_at, step_seconds, status, parameters_payload,
                    signal_count, result_checksum, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'succeeded', ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    methodology["id"],
                    result.scenario_code,
                    result.engine_version,
                    _utc_text(result.started_at),
                    _utc_text(result.ended_at),
                    round(result.step.total_seconds()),
                    json.dumps(
                        {"minimum_coverage": _decimal_text(result.minimum_coverage)},
                        sort_keys=True,
                    ),
                    len(result.signals),
                    result.checksum,
                    _utc_text(datetime.now(timezone.utc)),
                ),
            ).fetchone()[0]
            connection.executemany(
                """
                INSERT INTO cr_replay_signals(
                    run_id, signal_at, signal_score_text, scenario_status,
                    data_confidence, coverage_text, input_count, backtest_eligible,
                    eligibility_reason, latest_released_at, evidence_payload
                    , observation_ids_payload, input_checksum
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        _utc_text(item.signal_at),
                        _decimal_text(item.signal_score),
                        item.scenario_status,
                        item.data_confidence,
                        _decimal_text(item.coverage),
                        item.input_count,
                        int(item.backtest_eligible),
                        item.eligibility_reason,
                        None
                        if item.latest_released_at is None
                        else _utc_text(item.latest_released_at),
                        json.dumps(item.evidence, ensure_ascii=False),
                        json.dumps(item.observation_ids),
                        item.input_checksum,
                    )
                    for item in result.signals
                ],
            )
        return int(run_id)

    def replay_run_payload(self, run_id: int, *, signal_limit: int = 1000) -> dict | None:
        if signal_limit < 1 or signal_limit > 5000:
            raise ValueError("signal_limit must be between 1 and 5000")
        with self.db.connect() as connection:
            run = connection.execute(
                """
                SELECT replay.*, methodology.code AS methodology_code,
                       methodology.version AS methodology_version
                FROM cr_replay_runs AS replay
                JOIN cr_methodology_versions AS methodology
                  ON methodology.id = replay.methodology_id
                WHERE replay.id = ?
                """,
                (run_id,),
            ).fetchone()
            if run is None:
                return None
            rows = connection.execute(
                """
                SELECT signal_at, signal_score_text, scenario_status, data_confidence,
                       coverage_text, input_count, backtest_eligible, eligibility_reason,
                       latest_released_at, evidence_payload
                       , observation_ids_payload, input_checksum
                FROM cr_replay_signals WHERE run_id = ?
                ORDER BY signal_at DESC LIMIT ?
                """,
                (run_id, signal_limit),
            ).fetchall()
        return {
            "run_id": int(run["id"]),
            "scenario_code": run["scenario_code"],
            "engine_version": run["engine_version"],
            "methodology": {
                "code": run["methodology_code"],
                "version": run["methodology_version"],
            },
            "started_at": run["started_at"],
            "ended_at": run["ended_at"],
            "step_seconds": int(run["step_seconds"]),
            "status": run["status"],
            "parameters": json.loads(run["parameters_payload"] or "{}"),
            "signal_count": int(run["signal_count"]),
            "result_checksum": run["result_checksum"],
            "completed_at": run["completed_at"],
            "signals": [
                {
                    **dict(item),
                    "backtest_eligible": bool(item["backtest_eligible"]),
                    "evidence": json.loads(item["evidence_payload"] or "[]"),
                    "observation_ids": json.loads(item["observation_ids_payload"] or "[]"),
                }
                for item in reversed(rows)
            ],
        }

    def replay_signal_points(self, run_id: int) -> tuple[SignalPoint, ...]:
        with self.db.connect() as connection:
            run = connection.execute(
                "SELECT scenario_code FROM cr_replay_runs WHERE id = ? AND status = 'succeeded'",
                (run_id,),
            ).fetchone()
            if run is None:
                raise LookupError("successful replay run not found")
            rows = connection.execute(
                """
                SELECT signal_at, signal_score_text FROM cr_replay_signals
                WHERE run_id = ? AND backtest_eligible = 1
                ORDER BY signal_at
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            SignalPoint(
                scenario_code=run["scenario_code"],
                predicted_at=datetime.fromisoformat(item["signal_at"]),
                signal_score=Decimal(item["signal_score_text"]),
            )
            for item in rows
        )

    def link_backtest_provenance(
        self, run_id: int, *, replay_run_id: int, event_catalog_id: int
    ) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO cr_backtest_provenance(run_id, replay_run_id, event_catalog_id)
                VALUES (?, ?, ?)
                """,
                (run_id, replay_run_id, event_catalog_id),
            )

    def backtest_run_payload(self, run_id: int, *, prediction_limit: int = 500) -> dict | None:
        if prediction_limit < 1 or prediction_limit > 1000:
            raise ValueError("prediction_limit must be between 1 and 1000")
        with self.db.connect() as connection:
            run = connection.execute(
                """
                SELECT run.*, methodology.code AS methodology_code,
                       methodology.version AS methodology_version
                FROM cr_backtest_runs AS run
                JOIN cr_methodology_versions AS methodology ON methodology.id = run.methodology_id
                WHERE run.id = ?
                """,
                (run_id,),
            ).fetchone()
            if run is None:
                return None
            prediction_rows = connection.execute(
                """
                SELECT predicted_at, horizon_end, event_at, signal_score_text, outcome,
                       calibrated_probability_text, baseline_probability_text, confidence,
                       training_sample_count, calibration_bin, latest_training_horizon_end
                FROM cr_backtest_predictions WHERE run_id = ?
                ORDER BY predicted_at DESC LIMIT ?
                """,
                (run_id, prediction_limit),
            ).fetchall()
            predictions = list(reversed(prediction_rows))
            bins = connection.execute(
                """
                SELECT bin_index, lower_text, upper_text, prediction_count,
                       average_probability_text, observed_rate_text
                FROM cr_calibration_bins WHERE run_id = ? ORDER BY bin_index
                """,
                (run_id,),
            ).fetchall()
            lineage = connection.execute(
                """
                SELECT provenance.replay_run_id, provenance.event_catalog_id,
                       replay.result_checksum AS replay_checksum,
                       catalog.version AS event_catalog_version,
                       catalog.checksum AS event_catalog_checksum
                FROM cr_backtest_provenance AS provenance
                JOIN cr_replay_runs AS replay ON replay.id = provenance.replay_run_id
                JOIN cr_event_catalog_versions AS catalog
                  ON catalog.id = provenance.event_catalog_id
                WHERE provenance.run_id = ?
                """,
                (run_id,),
            ).fetchone()
        return {
            "run_id": int(run["id"]),
            "status": run["status"],
            "scenario_code": run["scenario_code"],
            "horizon_seconds": int(run["horizon_seconds"]),
            "started_at": run["started_at"],
            "completed_at": run["completed_at"],
            "methodology": {
                "code": run["methodology_code"],
                "version": run["methodology_version"],
            },
            "parameters": json.loads(run["parameters_payload"] or "{}"),
            "metrics": json.loads(run["metrics_payload"] or "{}"),
            "predictions": [
                {
                    **dict(item),
                    "outcome": bool(item["outcome"]),
                }
                for item in predictions
            ],
            "calibration_bins": [dict(item) for item in bins],
            "lineage": None if lineage is None else dict(lineage),
        }

    def latest_backtest_payload(
        self,
        scenario_code: str,
        *,
        methodology_code: str | None = None,
        methodology_version: str | None = None,
        require_provenance: bool = False,
    ) -> dict | None:
        if (methodology_code is None) != (methodology_version is None):
            raise ValueError("methodology code and version must be provided together")
        joins = []
        clauses = ["run.scenario_code = ?", "run.status = 'succeeded'"]
        parameters: list[object] = [scenario_code]
        if methodology_code is not None:
            joins.append(
                "JOIN cr_methodology_versions AS methodology ON methodology.id = run.methodology_id"
            )
            clauses.extend(["methodology.code = ?", "methodology.version = ?"])
            parameters.extend([methodology_code, methodology_version])
        if require_provenance:
            joins.extend(
                [
                    "JOIN cr_backtest_provenance AS provenance ON provenance.run_id = run.id",
                    "JOIN cr_event_catalog_versions AS catalog "
                    "ON catalog.id = provenance.event_catalog_id",
                ]
            )
            clauses.append("catalog.status = 'active'")
        with self.db.connect() as connection:
            row = connection.execute(
                f"""
                SELECT run.id FROM cr_backtest_runs AS run
                {' '.join(joins)}
                WHERE {' AND '.join(clauses)}
                ORDER BY run.completed_at DESC, run.id DESC LIMIT 1
                """,
                parameters,
            ).fetchone()
        return None if row is None else self.backtest_run_payload(int(row["id"]))

    def latest_indicator_band(
        self, code: str, *, methodology_id: int
    ) -> IndicatorBand | None:
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT state.band
                FROM cr_indicator_states AS state
                JOIN cr_indicator_definitions AS indicator ON indicator.id = state.indicator_id
                WHERE indicator.code = ? AND state.methodology_id = ?
                ORDER BY state.snapshot_at DESC, state.id DESC
                LIMIT 1
                """,
                (code, methodology_id),
            ).fetchone()
        return None if row is None else IndicatorBand(row["band"])

    def latest_v2_stage_context(self, *, methodology_id: int) -> tuple[str | None, Decimal]:
        with self.db.connect() as connection:
            latest = connection.execute(
                """
                SELECT stage FROM cr_market_snapshots_v2
                WHERE methodology_id=? ORDER BY snapshot_at DESC, id DESC LIMIT 1
                """,
                (methodology_id,),
            ).fetchone()
            peak = connection.execute(
                """
                SELECT max(CAST(stress_intensity_text AS REAL))
                FROM cr_market_snapshots_v2 WHERE methodology_id=?
                """,
                (methodology_id,),
            ).fetchone()[0]
        return (
            None if latest is None else str(latest["stage"]),
            Decimal("0") if peak is None else Decimal(str(peak)),
        )

    def save_v2_shadow_snapshot(
        self,
        scores: tuple[IndicatorScoreV2, ...],
        stage: MarketStageV2,
        *,
        snapshot_at: datetime,
        candidate_methodology_id: int,
        baseline_methodology_id: int,
        baseline_stage: str,
        coverage_status: CoverageStatus | None,
    ) -> None:
        snapshot_text = _utc_text(snapshot_at)
        with self.db.connect() as connection:
            indicator_ids = {
                row["code"]: int(row["id"])
                for row in connection.execute(
                    "SELECT id, code FROM cr_indicator_definitions"
                ).fetchall()
            }
            for score in scores:
                indicator_id = indicator_ids.get(score.indicator_code)
                if indicator_id is None:
                    raise RuntimeError(f"unknown v2 score indicator: {score.indicator_code}")
                connection.execute(
                    """
                    INSERT INTO cr_indicator_scores_v2(
                        indicator_id, methodology_id, snapshot_at, scoring_version,
                        profile, economic_score_text, economic_band,
                        historical_score_text, historical_band, trend_score_text,
                        acceleration_score_text, persistence_score_text,
                        regime_score_text, data_quality_text, availability_text,
                        effective_score_text, effective_band, agreement,
                        history_count, lineage_payload, input_checksum
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(indicator_id, methodology_id, snapshot_at, scoring_version)
                    DO UPDATE SET
                        profile=excluded.profile,
                        economic_score_text=excluded.economic_score_text,
                        economic_band=excluded.economic_band,
                        historical_score_text=excluded.historical_score_text,
                        historical_band=excluded.historical_band,
                        trend_score_text=excluded.trend_score_text,
                        acceleration_score_text=excluded.acceleration_score_text,
                        persistence_score_text=excluded.persistence_score_text,
                        regime_score_text=excluded.regime_score_text,
                        data_quality_text=excluded.data_quality_text,
                        availability_text=excluded.availability_text,
                        effective_score_text=excluded.effective_score_text,
                        effective_band=excluded.effective_band,
                        agreement=excluded.agreement,
                        history_count=excluded.history_count,
                        lineage_payload=excluded.lineage_payload,
                        input_checksum=excluded.input_checksum
                    """,
                    (
                        indicator_id, candidate_methodology_id, snapshot_text,
                        SCORING_VERSION, score.profile,
                        format(score.economic_score, "f"), score.economic_band.value,
                        _decimal_text(score.historical_score),
                        None if score.historical_band is None else score.historical_band.value,
                        format(score.trend_score, "f"),
                        format(score.acceleration_score, "f"),
                        format(score.persistence_score, "f"),
                        format(score.regime_score, "f"),
                        format(score.data_quality, "f"), format(score.availability, "f"),
                        _decimal_text(score.effective_score),
                        None if score.effective_band is None else score.effective_band.value,
                        score.agreement.value, score.history_count,
                        json.dumps(score.lineage, sort_keys=True, separators=(",", ":"), default=str),
                        score.input_checksum,
                    ),
                )
            for group in stage.groups:
                connection.execute(
                    """
                    INSERT INTO cr_group_states_v2(
                        methodology_id, snapshot_at, stage_version, group_code,
                        cluster_code, score_text, band, subchannel_count,
                        active_subchannel_count, thin_group, contributors_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(methodology_id, snapshot_at, stage_version, group_code)
                    DO UPDATE SET
                        cluster_code=excluded.cluster_code,
                        score_text=excluded.score_text,
                        band=excluded.band,
                        subchannel_count=excluded.subchannel_count,
                        active_subchannel_count=excluded.active_subchannel_count,
                        thin_group=excluded.thin_group,
                        contributors_payload=excluded.contributors_payload
                    """,
                    (
                        candidate_methodology_id, snapshot_text, STAGE_VERSION,
                        group.group_code, group.cluster_code, format(group.score, "f"),
                        group.band.value, group.subchannel_count,
                        group.active_subchannel_count, int(group.thin_group),
                        json.dumps(group.contributors, separators=(",", ":")),
                    ),
                )
            connection.execute(
                """
                INSERT INTO cr_market_snapshots_v2(
                    methodology_id, snapshot_at, stage_version, stage,
                    calculated_stage, stress_intensity_text,
                    systemic_breadth_text, active_independent_clusters,
                    active_regions, anchor_confirmation, critical_anchor,
                    coverage_status, reasons_payload, input_checksum
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(methodology_id, snapshot_at, stage_version)
                DO UPDATE SET
                    stage=excluded.stage,
                    calculated_stage=excluded.calculated_stage,
                    stress_intensity_text=excluded.stress_intensity_text,
                    systemic_breadth_text=excluded.systemic_breadth_text,
                    active_independent_clusters=excluded.active_independent_clusters,
                    active_regions=excluded.active_regions,
                    anchor_confirmation=excluded.anchor_confirmation,
                    critical_anchor=excluded.critical_anchor,
                    coverage_status=excluded.coverage_status,
                    reasons_payload=excluded.reasons_payload,
                    input_checksum=excluded.input_checksum
                """,
                (
                    candidate_methodology_id, snapshot_text, STAGE_VERSION,
                    stage.stage, stage.calculated_stage,
                    format(stage.stress_intensity, "f"),
                    format(stage.systemic_breadth, "f"),
                    stage.active_independent_clusters, stage.active_regions,
                    int(stage.anchor_confirmation), int(stage.critical_anchor),
                    "not_evaluated" if coverage_status is None else coverage_status.value,
                    json.dumps(stage.reasons, separators=(",", ":")),
                    stage.input_checksum,
                ),
            )
            diff = {
                "stage_changed": baseline_stage != stage.stage,
                "baseline_stage": baseline_stage,
                "candidate_stage": stage.stage,
                "reasons": stage.reasons,
            }
            connection.execute(
                """
                INSERT INTO cr_shadow_comparisons(
                    snapshot_at, baseline_methodology_id, candidate_methodology_id,
                    baseline_stage, candidate_stage, intensity_text, breadth_text,
                    diff_payload, input_checksum
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_at, baseline_methodology_id, candidate_methodology_id)
                DO UPDATE SET
                    baseline_stage=excluded.baseline_stage,
                    candidate_stage=excluded.candidate_stage,
                    intensity_text=excluded.intensity_text,
                    breadth_text=excluded.breadth_text,
                    diff_payload=excluded.diff_payload,
                    input_checksum=excluded.input_checksum
                """,
                (
                    snapshot_text, baseline_methodology_id, candidate_methodology_id,
                    baseline_stage, stage.stage, format(stage.stress_intensity, "f"),
                    format(stage.systemic_breadth, "f"),
                    json.dumps(diff, sort_keys=True, separators=(",", ":")),
                    stage.input_checksum,
                ),
            )

    def latest_scenario_v2_context(
        self, *, methodology_id: int
    ) -> dict[str, tuple[str, Decimal]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT state.scenario_code, state.status, state.strength_text
                FROM cr_scenario_states_v2 AS state
                JOIN (
                    SELECT scenario_code, max(id) AS id
                    FROM cr_scenario_states_v2
                    WHERE methodology_id=? AND playbook_version=?
                    GROUP BY scenario_code
                ) AS latest ON latest.id=state.id
                """,
                (methodology_id, PLAYBOOK_VERSION),
            ).fetchall()
        return {
            row["scenario_code"]: (row["status"], Decimal(row["strength_text"]))
            for row in rows
        }

    def save_scenario_states_v2(
        self,
        states: tuple[ScenarioStateV2, ...],
        *,
        methodology_id: int,
        snapshot_at: datetime,
        baseline_stage: str,
    ) -> None:
        snapshot_text = _utc_text(snapshot_at)
        with self.db.connect() as connection:
            for state in states:
                previous = connection.execute(
                    """
                    SELECT status, strength_text, confirmed_groups_payload,
                           evidence_ids_payload, reasons_payload
                    FROM cr_scenario_states_v2
                    WHERE methodology_id=? AND scenario_code=? AND playbook_version=?
                      AND snapshot_at < ?
                    ORDER BY snapshot_at DESC, id DESC LIMIT 1
                    """,
                    (methodology_id, state.code, PLAYBOOK_VERSION, snapshot_text),
                ).fetchone()
                previous_groups = set(
                    json.loads(previous["confirmed_groups_payload"] or "[]")
                    if previous is not None else ()
                )
                previous_evidence = set(
                    json.loads(previous["evidence_ids_payload"] or "[]")
                    if previous is not None else ()
                )
                current_groups = set(state.confirmed_groups)
                current_evidence = set(state.evidence_ids)
                causal_diff = {
                    "from_status": None if previous is None else previous["status"],
                    "to_status": state.status,
                    "strength_change": None
                    if previous is None
                    else format(state.strength - Decimal(previous["strength_text"]), "f"),
                    "added_confirmations": sorted(current_groups - previous_groups),
                    "removed_confirmations": sorted(previous_groups - current_groups),
                    "new_evidence_ids": sorted(current_evidence - previous_evidence),
                    "expired_evidence_ids": sorted(previous_evidence - current_evidence),
                    "reasons": state.reasons,
                }
                connection.execute(
                    """
                    INSERT INTO cr_scenario_states_v2(
                        methodology_id, snapshot_at, playbook_version, scenario_code,
                        status, strength_text, reliability_text,
                        active_independent_clusters, current_chain_step,
                        confirmed_groups_payload, missing_anchors_payload,
                        next_confirmations_payload, recovery_confirmations_payload,
                        evidence_ids_payload, reasons_payload, causal_diff_payload,
                        input_checksum
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(methodology_id, snapshot_at, playbook_version, scenario_code)
                    DO UPDATE SET
                        status=excluded.status, strength_text=excluded.strength_text,
                        reliability_text=excluded.reliability_text,
                        active_independent_clusters=excluded.active_independent_clusters,
                        current_chain_step=excluded.current_chain_step,
                        confirmed_groups_payload=excluded.confirmed_groups_payload,
                        missing_anchors_payload=excluded.missing_anchors_payload,
                        next_confirmations_payload=excluded.next_confirmations_payload,
                        recovery_confirmations_payload=excluded.recovery_confirmations_payload,
                        evidence_ids_payload=excluded.evidence_ids_payload,
                        reasons_payload=excluded.reasons_payload,
                        causal_diff_payload=excluded.causal_diff_payload,
                        input_checksum=excluded.input_checksum
                    """,
                    (
                        methodology_id, snapshot_text, PLAYBOOK_VERSION, state.code,
                        state.status, format(state.strength, "f"),
                        format(state.reliability, "f"), state.active_independent_clusters,
                        state.current_chain_step,
                        json.dumps(state.confirmed_groups, separators=(",", ":")),
                        json.dumps(state.missing_anchors, separators=(",", ":")),
                        json.dumps(state.next_confirmations, separators=(",", ":")),
                        json.dumps(state.recovery_confirmations, separators=(",", ":")),
                        json.dumps(state.evidence_ids, separators=(",", ":")),
                        json.dumps(state.reasons, separators=(",", ":")),
                        json.dumps(causal_diff, sort_keys=True, separators=(",", ":")),
                        state.input_checksum,
                    ),
                )
                self._update_signal_scorecard(
                    connection,
                    methodology_id=methodology_id,
                    state=state,
                    snapshot_text=snapshot_text,
                    baseline_stage=baseline_stage,
                )

    @staticmethod
    def _update_signal_scorecard(
        connection,
        *,
        methodology_id: int,
        state: ScenarioStateV2,
        snapshot_text: str,
        baseline_stage: str,
    ) -> None:
        active_statuses = {"watch", "elevated", "confirmed", "recovery_watch", "recovery_confirmed"}
        open_signal = connection.execute(
            """
            SELECT * FROM cr_signal_scorecards
            WHERE methodology_id=? AND scenario_code=? AND outcome_status='open'
            ORDER BY id DESC LIMIT 1
            """,
            (methodology_id, state.code),
        ).fetchone()
        if state.status not in active_statuses:
            if open_signal is not None:
                connection.execute(
                    """
                    UPDATE cr_signal_scorecards
                    SET invalidated_at=?, outcome_status='invalidated',
                        last_seen_at=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (snapshot_text, snapshot_text, open_signal["id"]),
                )
            return
        if open_signal is None:
            signal_key = hashlib.sha256(
                f"{state.code}|{snapshot_text}".encode()
            ).hexdigest()
            connection.execute(
                """
                INSERT INTO cr_signal_scorecards(
                    methodology_id, scenario_code, signal_key, first_detected_at,
                    first_elevated_at, first_confirmed_at, last_seen_at,
                    peak_strength_text, baseline_stage, attribution_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    methodology_id, state.code, signal_key, snapshot_text,
                    snapshot_text if state.status in {"elevated", "confirmed"} else None,
                    snapshot_text if state.status == "confirmed" else None,
                    snapshot_text, format(state.strength, "f"), baseline_stage,
                    json.dumps(
                        {"confirmed_groups": state.confirmed_groups, "evidence_ids": state.evidence_ids},
                        sort_keys=True,
                    ),
                ),
            )
            return
        connection.execute(
            """
            UPDATE cr_signal_scorecards
            SET last_seen_at=?,
                first_elevated_at=COALESCE(first_elevated_at, ?),
                first_confirmed_at=COALESCE(first_confirmed_at, ?),
                peak_strength_text=CASE
                    WHEN CAST(peak_strength_text AS REAL) < ? THEN ? ELSE peak_strength_text END,
                attribution_payload=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                snapshot_text,
                snapshot_text if state.status in {"elevated", "confirmed"} else None,
                snapshot_text if state.status == "confirmed" else None,
                float(state.strength), format(state.strength, "f"),
                json.dumps(
                    {"confirmed_groups": state.confirmed_groups, "evidence_ids": state.evidence_ids},
                    sort_keys=True,
                ),
                open_signal["id"],
            ),
        )

    def scenario_v2_payload(self, *, methodology_version: str, locale: str) -> dict:
        if locale not in {"ru", "en"}:
            raise ValueError("locale must be ru or en")
        with self.db.connect() as connection:
            methodology = connection.execute(
                "SELECT id FROM cr_methodology_versions WHERE version=?",
                (methodology_version,),
            ).fetchone()
            if methodology is None:
                return {"ready": False, "items": []}
            snapshot = connection.execute(
                """
                SELECT max(snapshot_at) FROM cr_scenario_states_v2
                WHERE methodology_id=? AND playbook_version=?
                """,
                (methodology["id"], PLAYBOOK_VERSION),
            ).fetchone()[0]
            if snapshot is None:
                return {"ready": False, "items": []}
            rows = connection.execute(
                """
                SELECT state.*, metadata.name_ru, metadata.name_en,
                       metadata.description_ru AS registry_description_ru,
                       metadata.description_en AS registry_description_en,
                       metadata.calculation_ru, metadata.calculation_en,
                       metadata.limitations_ru AS registry_limitations_ru,
                       metadata.limitations_en AS registry_limitations_en
                FROM cr_scenario_states_v2 AS state
                LEFT JOIN cr_entity_metadata AS metadata
                  ON metadata.entity_type='scenario'
                 AND metadata.entity_code=state.scenario_code
                 AND metadata.metadata_version='v11'
                WHERE state.methodology_id=? AND state.playbook_version=?
                  AND state.snapshot_at=?
                ORDER BY CAST(state.strength_text AS REAL) DESC, state.scenario_code
                """,
                (methodology["id"], PLAYBOOK_VERSION, snapshot),
            ).fetchall()
            group_metadata_rows = connection.execute(
                """
                SELECT entity_code, name_ru, name_en
                FROM cr_entity_metadata
                WHERE entity_type='group' AND metadata_version='v11'
                """
            ).fetchall()
        group_names = {
            row["entity_code"]: row[f"name_{locale}"] or row["entity_code"]
            for row in group_metadata_rows
        }
        items = []
        for row in rows:
            playbook = playbook_for(row["scenario_code"])
            confirmed_groups = json.loads(row["confirmed_groups_payload"])
            next_confirmations = json.loads(row["next_confirmations_payload"])
            missing_anchors = json.loads(row["missing_anchors_payload"])
            items.append(
                {
                    "code": row["scenario_code"], "status": row["status"],
                    "name": row[f"name_{locale}"] or row["scenario_code"],
                    "strength": row["strength_text"], "reliability": row["reliability_text"],
                    "description": getattr(playbook, f"description_{locale}"),
                    "calculation": row[f"calculation_{locale}"] or "",
                    "causal_chain": getattr(playbook, f"causal_chain_{locale}"),
                    "current_chain_step": int(row["current_chain_step"]),
                    "confirmed_groups": confirmed_groups,
                    "confirmed_group_details": [
                        {"code": code, "name": group_names.get(code, code)}
                        for code in confirmed_groups
                    ],
                    "missing_anchors": missing_anchors,
                    "missing_anchor_details": [
                        {"code": code, "name": group_names.get(code, code)}
                        for code in missing_anchors
                    ],
                    "next_confirmations": next_confirmations,
                    "next_confirmation_details": [
                        {"code": code, "name": group_names.get(code, code)}
                        for code in next_confirmations
                    ],
                    "invalidation": getattr(playbook, f"invalidation_{locale}"),
                    "recovery_conditions": getattr(playbook, f"recovery_{locale}"),
                    "recovery_confirmations": json.loads(row["recovery_confirmations_payload"]),
                    "vulnerable_assets": playbook.vulnerable_assets,
                    "possible_beneficiaries": playbook.possible_beneficiaries,
                    "limitations": getattr(playbook, f"limitations_{locale}"),
                    "evidence_ids": json.loads(row["evidence_ids_payload"]),
                    "what_changed": json.loads(row["causal_diff_payload"]),
                }
            )
        return {
            "ready": True, "as_of": snapshot, "playbook_version": PLAYBOOK_VERSION,
            "probability": None, "items": items,
        }

    def open_trade_exposure_inputs(self, *, user_id: int) -> tuple[dict, ...]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, symbol, side, leverage, quantity, entry_price, stop_price
                FROM trades WHERE user_id=? AND status='open'
                ORDER BY opened_at DESC, id DESC
                """,
                (user_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def latest_v2_overview_payload(self, *, methodology_version: str, locale: str) -> dict:
        if locale not in {"ru", "en"}:
            raise ValueError("locale must be ru or en")
        with self.db.connect() as connection:
            snapshot = connection.execute(
                """
                SELECT snapshot.*, methodology.code AS methodology_code,
                       methodology.version AS methodology_version
                FROM cr_market_snapshots_v2 AS snapshot
                JOIN cr_methodology_versions AS methodology
                  ON methodology.id=snapshot.methodology_id
                WHERE methodology.version=?
                ORDER BY snapshot.snapshot_at DESC, snapshot.id DESC LIMIT 1
                """,
                (methodology_version,),
            ).fetchone()
            if snapshot is None:
                return {"ready": False, "methodology": methodology_version, "items": []}
            rows = connection.execute(
                """
                SELECT score.*, indicator.code, indicator.name,
                       indicator.unit, indicator.max_staleness_seconds,
                       source.name AS source_name,
                       threshold.warning_value, threshold.danger_value,
                       threshold.critical_value, threshold.source_url,
                       observation.value_text AS current_value_text,
                       observation.observed_at, observation.released_at,
                       metadata.name_ru, metadata.name_en, metadata.description_ru,
                       metadata.description_en, metadata.why_it_matters_ru,
                       metadata.why_it_matters_en, metadata.worse_when_ru,
                       metadata.worse_when_en, metadata.calculation_ru,
                       metadata.calculation_en, metadata.limitations_ru,
                       metadata.limitations_en, metadata.technical_code
                FROM cr_indicator_scores_v2 AS score
                JOIN cr_indicator_definitions AS indicator ON indicator.id=score.indicator_id
                JOIN cr_sources AS source ON source.id=indicator.source_id
                LEFT JOIN cr_observations AS observation ON observation.id = (
                    SELECT latest.id FROM cr_observations AS latest
                    WHERE latest.indicator_id=indicator.id
                      AND latest.released_at<=score.snapshot_at
                    ORDER BY latest.observed_at DESC, latest.released_at DESC,
                             latest.fetched_at DESC, latest.id DESC LIMIT 1
                )
                JOIN cr_threshold_sets AS threshold
                  ON threshold.indicator_id=indicator.id
                 AND threshold.methodology_id=score.methodology_id
                 AND threshold.scope='system' AND threshold.owner_user_id=0
                LEFT JOIN cr_entity_metadata AS metadata
                  ON metadata.entity_type='indicator' AND metadata.entity_code=indicator.code
                 AND metadata.metadata_version='v11'
                WHERE score.methodology_id=? AND score.snapshot_at=?
                ORDER BY CAST(COALESCE(score.effective_score_text, '0') AS REAL) DESC,
                         indicator.code
                """,
                (snapshot["methodology_id"], snapshot["snapshot_at"]),
            ).fetchall()
            group_rows = connection.execute(
                """
                SELECT state.*, metadata.name_ru, metadata.name_en,
                       metadata.description_ru, metadata.description_en,
                       metadata.calculation_ru, metadata.calculation_en,
                       metadata.limitations_ru, metadata.limitations_en
                FROM cr_group_states_v2 AS state
                LEFT JOIN cr_entity_metadata AS metadata
                  ON metadata.entity_type='group' AND metadata.entity_code=state.group_code
                 AND metadata.metadata_version='v11'
                WHERE state.methodology_id=? AND state.snapshot_at=?
                ORDER BY CAST(state.score_text AS REAL) DESC, state.group_code
                """,
                (snapshot["methodology_id"], snapshot["snapshot_at"]),
            ).fetchall()
            news_coverage = connection.execute(
                """
                SELECT * FROM cr_news_coverage_snapshots
                WHERE methodology_id=? AND snapshot_at<=?
                ORDER BY snapshot_at DESC, id DESC LIMIT 1
                """,
                (snapshot["methodology_id"], snapshot["snapshot_at"]),
            ).fetchone()
        suffix = "ru" if locale == "ru" else "en"
        as_of = datetime.fromisoformat(snapshot["snapshot_at"])
        items = []
        for row in rows:
            freshness, age_seconds = _freshness_from_release(
                row["released_at"],
                as_of=as_of,
                max_staleness_seconds=int(row["max_staleness_seconds"]),
            )
            items.append(
                {
                    "code": row["code"],
                    "name": row[f"name_{suffix}"] or row["name"],
                    "english_name": row["name_en"] or row["name"],
                    "description": row[f"description_{suffix}"] or "",
                    "why_it_matters": row[f"why_it_matters_{suffix}"] or "",
                    "worse_when": row[f"worse_when_{suffix}"] or "",
                    "calculation": row[f"calculation_{suffix}"] or "",
                    "limitations": row[f"limitations_{suffix}"] or "",
                    "technical_code": row["technical_code"] or row["code"],
                    "value": row["current_value_text"],
                    "unit": row["unit"],
                    "freshness": freshness,
                    "age_seconds": age_seconds,
                    "observed_at": row["observed_at"],
                    "released_at": row["released_at"],
                    "thresholds": {
                        "warning": row["warning_value"],
                        "danger": row["danger_value"],
                        "critical": row["critical_value"],
                    },
                    "source_name": row["source_name"],
                    "source_url": row["source_url"],
                    "profile": row["profile"],
                    "economic_band": row["economic_band"],
                    "historical_band": row["historical_band"],
                    "effective_band": row["effective_band"],
                    "agreement": row["agreement"],
                    "effective_score": row["effective_score_text"],
                    "history_count": int(row["history_count"]),
                }
            )
        news_payload = None if news_coverage is None else {
            "status": news_coverage["status"],
            "ratio": news_coverage["ratio_text"],
            "expected_source_count": int(news_coverage["expected_source_count"]),
            "healthy_source_count": int(news_coverage["healthy_source_count"]),
            "degraded_source_count": int(news_coverage["degraded_source_count"]),
            "failed_source_count": int(news_coverage["failed_source_count"]),
            "missing_regions": json.loads(news_coverage["missing_regions_payload"] or "[]"),
        }
        return {
            "ready": True,
            "as_of": snapshot["snapshot_at"],
            "methodology": {
                "code": snapshot["methodology_code"],
                "version": snapshot["methodology_version"],
                "stage_version": snapshot["stage_version"],
                "promotion_status": "shadow_candidate",
            },
            "stage": snapshot["stage"],
            "calculated_stage": snapshot["calculated_stage"],
            "stress_intensity": snapshot["stress_intensity_text"],
            "systemic_breadth": snapshot["systemic_breadth_text"],
            "active_independent_clusters": int(snapshot["active_independent_clusters"]),
            "active_regions": int(snapshot["active_regions"]),
            "coverage_status": snapshot["coverage_status"],
            "news_coverage": news_payload,
            "groups": [
                {
                    "code": row["group_code"],
                    "name": row[f"name_{suffix}"] or row["group_code"],
                    "english_name": row["name_en"] or row["group_code"],
                    "description": row[f"description_{suffix}"] or "",
                    "calculation": row[f"calculation_{suffix}"] or "",
                    "limitations": row[f"limitations_{suffix}"] or "",
                    "score": row["score_text"],
                    "band": row["band"],
                    "subchannel_count": int(row["subchannel_count"]),
                    "active_subchannel_count": int(row["active_subchannel_count"]),
                    "thin_group": bool(row["thin_group"]),
                    "contributors": json.loads(row["contributors_payload"]),
                }
                for row in group_rows
            ],
            "items": items,
        }

    def resolve_signal_scorecards(
        self,
        *,
        methodology_version: str,
        as_of: datetime,
    ) -> dict:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        horizon_days = {
            "global_recession": 365,
            "financial_stress": 90,
            "oil_stagflation": 180,
            "crypto_leverage_unwind": 30,
            "china_hard_landing": 180,
        }
        resolved = false_alerts = skipped = 0
        with self.db.connect() as connection:
            methodology = connection.execute(
                "SELECT id FROM cr_methodology_versions WHERE version=?",
                (methodology_version,),
            ).fetchone()
            if methodology is None:
                return {"resolved": 0, "false_alerts": 0, "skipped": 0}
            rows = connection.execute(
                """
                SELECT * FROM cr_signal_scorecards
                WHERE methodology_id=? AND outcome_status='invalidated'
                ORDER BY id
                """,
                (methodology["id"],),
            ).fetchall()
            for row in rows:
                days = horizon_days.get(row["scenario_code"])
                if days is None:
                    skipped += 1
                    continue
                first = datetime.fromisoformat(row["first_detected_at"])
                deadline = first + timedelta(days=days)
                if as_of < deadline:
                    skipped += 1
                    continue
                catalog = connection.execute(
                    """
                    SELECT id FROM cr_event_catalog_versions
                    WHERE scenario_code=? AND status='active'
                    ORDER BY id DESC LIMIT 1
                    """,
                    (row["scenario_code"],),
                ).fetchone()
                if catalog is None:
                    skipped += 1
                    continue
                event = connection.execute(
                    """
                    SELECT event_code, started_at FROM cr_event_labels
                    WHERE catalog_id=? AND started_at > ? AND started_at <= ?
                    ORDER BY started_at LIMIT 1
                    """,
                    (catalog["id"], _utc_text(first), _utc_text(deadline)),
                ).fetchone()
                if event is not None:
                    event_at = datetime.fromisoformat(event["started_at"])
                    lead_days = Decimal(
                        str((event_at - first).total_seconds() / 86400)
                    ).quantize(Decimal(".01"))
                    outcome = "resolved"
                    resolved += 1
                elif row["first_elevated_at"] is not None or row["first_confirmed_at"] is not None:
                    lead_days = None
                    outcome = "false_alert"
                    false_alerts += 1
                else:
                    skipped += 1
                    continue
                reaction = {
                    "outcome_event_code": None if event is None else event["event_code"],
                    "lead_days": None if lead_days is None else format(lead_days, "f"),
                    "market_reaction_status": "unavailable_without_validated_asset_history",
                    "mfe": None,
                    "mae": None,
                }
                connection.execute(
                    """
                    UPDATE cr_signal_scorecards
                    SET outcome_status=?, reaction_payload=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (outcome, json.dumps(reaction, sort_keys=True), row["id"]),
                )
        return {"resolved": resolved, "false_alerts": false_alerts, "skipped": skipped}

    def signal_scorecards_payload(
        self,
        *,
        methodology_version: str,
        limit: int = 100,
    ) -> dict:
        if limit < 1 or limit > 500:
            raise ValueError("scorecard limit must be between 1 and 500")
        with self.db.connect() as connection:
            methodology = connection.execute(
                "SELECT id FROM cr_methodology_versions WHERE version=?",
                (methodology_version,),
            ).fetchone()
            if methodology is None:
                return {"ready": False, "items": []}
            rows = connection.execute(
                """
                SELECT * FROM cr_signal_scorecards WHERE methodology_id=?
                ORDER BY first_detected_at DESC, id DESC LIMIT ?
                """,
                (methodology["id"], limit),
            ).fetchall()
        return {
            "ready": True,
            "methodology_version": methodology_version,
            "items": [
                {
                    **dict(row),
                    "reaction": json.loads(row["reaction_payload"] or "{}"),
                    "attribution": json.loads(row["attribution_payload"] or "{}"),
                }
                for row in rows
            ],
        }

    def save_analysis_snapshot(
        self,
        states: list[IndicatorState],
        overview: MarketOverview,
        *,
        methodology_id: int,
    ) -> None:
        snapshot_at = _utc_text(overview.snapshot_at)
        quality_scores = {
            DataFreshness.FRESH: "1",
            DataFreshness.DELAYED: "0.7",
            DataFreshness.STALE: "0.2",
            DataFreshness.MISSING: "0",
        }
        with self.db.connect() as connection:
            for state in states:
                resolved = connection.execute(
                    """
                    SELECT indicator.id, observation.id
                    FROM cr_indicator_definitions AS indicator
                    JOIN cr_sources AS source ON source.id = indicator.source_id
                    JOIN cr_observations AS observation
                        ON observation.indicator_id = indicator.id AND observation.source_id = source.id
                    WHERE indicator.code = ? AND source.code = ?
                        AND observation.observed_at = ? AND observation.vintage = ?
                    """,
                    (
                        state.indicator_code,
                        state.observation.source_code,
                        _utc_text(state.observation.observed_at),
                        state.observation.vintage,
                    ),
                ).fetchone()
                if resolved is None:
                    raise RuntimeError(f"cannot persist unresolved indicator state: {state.indicator_code}")
                payload = json.dumps(
                    {
                        "distance_to_next": None
                        if state.distance_to_next is None
                        else format(state.distance_to_next, "f"),
                        "source": state.observation.source_code,
                        "released_at": _utc_text(state.observation.released_at),
                        "stability": {
                            "raw_band": (state.raw_band or state.band).value,
                            "effective_band": state.band.value,
                            "persistence_count": state.persistence_count,
                            "confirmation_required": state.confirmation_required,
                            "held_by_hysteresis": state.held_by_hysteresis,
                        },
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
                connection.execute(
                    """
                    INSERT INTO cr_indicator_states(
                        indicator_id, methodology_id, observation_id, snapshot_at,
                        value_text, band, distance_to_next_text, stress_score_text,
                        freshness, quality_score_text, explanation_payload,
                        persistence_count, raw_band, held_by_hysteresis,
                        confirmation_required
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(indicator_id, methodology_id, snapshot_at) DO UPDATE SET
                        observation_id=excluded.observation_id,
                        value_text=excluded.value_text,
                        band=excluded.band,
                        distance_to_next_text=excluded.distance_to_next_text,
                        stress_score_text=excluded.stress_score_text,
                        freshness=excluded.freshness,
                        quality_score_text=excluded.quality_score_text,
                        explanation_payload=excluded.explanation_payload,
                        persistence_count=excluded.persistence_count,
                        raw_band=excluded.raw_band,
                        held_by_hysteresis=excluded.held_by_hysteresis,
                        confirmation_required=excluded.confirmation_required
                    """,
                    (
                        resolved[0],
                        methodology_id,
                        resolved[1],
                        snapshot_at,
                        format(state.value, "f"),
                        state.band.value,
                        None if state.distance_to_next is None else format(state.distance_to_next, "f"),
                        format(state.stress_score, "f"),
                        state.freshness.value,
                        quality_scores[state.freshness],
                        payload,
                        state.persistence_count,
                        (state.raw_band or state.band).value,
                        int(state.held_by_hysteresis),
                        state.confirmation_required,
                    ),
                )
            for group in overview.groups:
                connection.execute(
                    """
                    INSERT INTO cr_group_states(
                        group_code, methodology_id, snapshot_at, band, stress_score_text,
                        indicator_count, worsening_count, contributors_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(group_code, methodology_id, snapshot_at) DO UPDATE SET
                        band=excluded.band,
                        stress_score_text=excluded.stress_score_text,
                        indicator_count=excluded.indicator_count,
                        worsening_count=excluded.worsening_count,
                        contributors_payload=excluded.contributors_payload
                    """,
                    (
                        group.group_code,
                        methodology_id,
                        snapshot_at,
                        group.band.value,
                        format(group.stress_score, "f"),
                        group.indicator_count,
                        group.worsening_count,
                        json.dumps(group.contributors, separators=(",", ":")),
                    ),
                )
            explanation = json.dumps(
                {"ru": overview.explanation_ru, "en": overview.explanation_en},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            coverage = overview.coverage
            coverage_status = "not_evaluated" if coverage is None else coverage.status.value
            coverage_ratio = None if coverage is None else format(coverage.ratio, "f")
            coverage_payload = json.dumps(
                {}
                if coverage is None
                else {
                    "expected_count": coverage.expected_count,
                    "available_count": coverage.available_count,
                    "fresh_count": coverage.fresh_count,
                    "delayed_count": coverage.delayed_count,
                    "stale_count": coverage.stale_count,
                    "missing_count": coverage.missing_count,
                    "available_group_codes": sorted(coverage.available_group_codes),
                    "missing_required_groups": coverage.missing_required_groups,
                    "missing_required_regions": coverage.missing_required_regions,
                    "reason_codes": coverage.reason_codes,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            connection.execute(
                """
                INSERT INTO cr_market_snapshots(
                    methodology_id, snapshot_at, stage, calculated_stage,
                    coverage_status, coverage_ratio_text, coverage_payload, active_group_count,
                    warning_group_count, danger_group_count, critical_group_count,
                    explanation_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(methodology_id, snapshot_at) DO UPDATE SET
                    stage=excluded.stage,
                    calculated_stage=excluded.calculated_stage,
                    coverage_status=excluded.coverage_status,
                    coverage_ratio_text=excluded.coverage_ratio_text,
                    coverage_payload=excluded.coverage_payload,
                    active_group_count=excluded.active_group_count,
                    warning_group_count=excluded.warning_group_count,
                    danger_group_count=excluded.danger_group_count,
                    critical_group_count=excluded.critical_group_count,
                    explanation_payload=excluded.explanation_payload
                """,
                (
                    methodology_id,
                    snapshot_at,
                    overview.stage.value,
                    overview.calculated_stage.value,
                    coverage_status,
                    coverage_ratio,
                    coverage_payload,
                    overview.active_group_count,
                    overview.warning_group_count,
                    overview.danger_group_count,
                    overview.critical_group_count,
                    explanation,
                ),
            )

    def save_scenario_snapshot(
        self,
        states: tuple[ScenarioState, ...],
        *,
        methodology_id: int,
        snapshot_at: datetime,
    ) -> None:
        snapshot_text = _utc_text(snapshot_at)
        status_rank = {"unknown": 0, "inactive": 0, "watch": 1, "elevated": 2, "confirmed": 3}
        with self.db.connect() as connection:
            for state in states:
                scenario = connection.execute(
                    """
                    SELECT id FROM cr_scenario_definitions
                    WHERE code = ? AND methodology_id = ? AND enabled = 1
                    """,
                    (state.code, methodology_id),
                ).fetchone()
                if scenario is None:
                    raise RuntimeError(f"unregistered scenario: {state.code}")
                previous = connection.execute(
                    """
                    SELECT status FROM cr_scenario_states
                    WHERE scenario_id = ? AND methodology_id = ? AND snapshot_at < ?
                    ORDER BY snapshot_at DESC, id DESC LIMIT 1
                    """,
                    (scenario[0], methodology_id, snapshot_text),
                ).fetchone()
                evidence = json.dumps(
                    [{"group_code": code, "band": band.value} for code, band in state.evidence],
                    separators=(",", ":"),
                    sort_keys=True,
                )
                explanation = json.dumps(
                    {"ru": state.explanation_ru, "en": state.explanation_en},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                connection.execute(
                    """
                    INSERT INTO cr_scenario_states(
                        scenario_id, methodology_id, snapshot_at, status, confidence,
                        active_group_count, evidence_payload, explanation_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scenario_id, methodology_id, snapshot_at) DO UPDATE SET
                        status=excluded.status,
                        confidence=excluded.confidence,
                        active_group_count=excluded.active_group_count,
                        evidence_payload=excluded.evidence_payload,
                        explanation_payload=excluded.explanation_payload
                    """,
                    (
                        scenario[0],
                        methodology_id,
                        snapshot_text,
                        state.status.value,
                        state.confidence.value,
                        state.active_group_count,
                        evidence,
                        explanation,
                    ),
                )
                if previous is None or previous["status"] == state.status.value:
                    continue
                before = previous["status"]
                after = state.status.value
                escalation = status_rank[after] > status_rank[before] and after in {
                    "elevated",
                    "confirmed",
                }
                recovery = status_rank[after] < status_rank[before] and before in {
                    "elevated",
                    "confirmed",
                }
                if not escalation and not recovery:
                    continue
                event_type = "scenario_escalation" if escalation else "scenario_recovery"
                severity = "critical" if after == "confirmed" else "warning" if escalation else "info"
                last_event = connection.execute(
                    """
                    SELECT severity, snapshot_at
                    FROM cr_alert_events
                    WHERE scenario_id = ? AND methodology_id = ? AND event_type = ?
                      AND snapshot_at < ?
                    ORDER BY snapshot_at DESC, id DESC
                    LIMIT 1
                    """,
                    (scenario[0], methodology_id, event_type, snapshot_text),
                ).fetchone()
                severity_rank = {"info": 0, "warning": 1, "critical": 2}
                if last_event is not None:
                    elapsed = datetime.fromisoformat(snapshot_text) - datetime.fromisoformat(
                        last_event["snapshot_at"]
                    )
                    cooldown_active = elapsed.total_seconds() < STABILITY_POLICY.alert_cooldown_seconds
                    no_more_severe = severity_rank[severity] <= severity_rank[last_event["severity"]]
                    if cooldown_active and no_more_severe:
                        continue
                payload = json.dumps(
                    {
                        "explanation": {"ru": state.explanation_ru, "en": state.explanation_en},
                        "evidence": json.loads(evidence),
                        "horizon": state.horizon,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                event_key = f"scenario:{state.code}:{snapshot_text}:{before}:{after}"
                connection.execute(
                    """
                    INSERT OR IGNORE INTO cr_alert_events(
                        event_key, event_type, severity, scenario_id, methodology_id,
                        snapshot_at, from_state, to_state, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_key,
                        event_type,
                        severity,
                        scenario[0],
                        methodology_id,
                        snapshot_text,
                        before,
                        after,
                        payload,
                    ),
                )

    def enqueue_alert_deliveries(self, user_ids: tuple[int, ...]) -> int:
        clean_ids = tuple(sorted({int(user_id) for user_id in user_ids if int(user_id) > 0}))
        if not clean_ids:
            return 0
        inserted = 0
        with self.db.connect() as connection:
            events = connection.execute("SELECT id FROM cr_alert_events ORDER BY id").fetchall()
            for event in events:
                for user_id in clean_ids:
                    cursor = connection.execute(
                        "INSERT OR IGNORE INTO cr_alert_deliveries(event_id, user_id) VALUES (?, ?)",
                        (event["id"], user_id),
                    )
                    inserted += cursor.rowcount
        return inserted

    def record_data_health_transition(
        self,
        *,
        methodology_id: int,
        snapshot_at: datetime,
        status: str,
        ratio: Decimal,
        missing_groups: tuple[str, ...],
        missing_regions: tuple[str, ...],
        reason_codes: tuple[str, ...],
    ) -> bool:
        if status not in {"healthy", "degraded", "insufficient_data"}:
            raise ValueError("unknown data health status")
        snapshot_text = _utc_text(snapshot_at)
        with self.db.connect() as connection:
            previous = connection.execute(
                """
                SELECT coverage_status, coverage_ratio_text FROM cr_market_snapshots
                WHERE methodology_id=? AND snapshot_at < ?
                  AND coverage_status != 'not_evaluated'
                ORDER BY snapshot_at DESC, id DESC LIMIT 1
                """,
                (methodology_id, snapshot_text),
            ).fetchone()
            # The first stored state establishes a baseline and never creates an alert.
            if previous is None or previous["coverage_status"] == status:
                return False
            payload = json.dumps(
                {
                    "ratio": str(ratio),
                    "previous_ratio": previous["coverage_ratio_text"],
                    "missing_groups": missing_groups,
                    "missing_regions": missing_regions,
                    "reason_codes": reason_codes,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            event_key = f"data-health:{methodology_id}:{snapshot_text}:{previous['coverage_status']}:{status}"
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO cr_data_health_events(
                    event_key, methodology_id, snapshot_at, from_status, to_status, payload
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_key,
                    methodology_id,
                    snapshot_text,
                    previous["coverage_status"],
                    status,
                    payload,
                ),
            )
            return cursor.rowcount == 1

    def enqueue_data_health_deliveries(self, user_ids: tuple[int, ...]) -> int:
        clean_ids = tuple(sorted({int(user_id) for user_id in user_ids if int(user_id) > 0}))
        inserted = 0
        with self.db.connect() as connection:
            events = connection.execute("SELECT id FROM cr_data_health_events ORDER BY id").fetchall()
            for event in events:
                for user_id in clean_ids:
                    inserted += connection.execute(
                        "INSERT OR IGNORE INTO cr_data_health_deliveries(event_id, user_id) VALUES (?, ?)",
                        (event["id"], user_id),
                    ).rowcount
        return inserted

    def pending_data_health_deliveries(self, *, limit: int = 20) -> list[DataHealthDelivery]:
        now = _utc_text(datetime.now(timezone.utc))
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT delivery.id AS delivery_id, delivery.user_id,
                       event.from_status, event.to_status, event.payload
                FROM cr_data_health_deliveries AS delivery
                JOIN cr_data_health_events AS event ON event.id=delivery.event_id
                WHERE delivery.attempts < 3 AND delivery.status IN ('pending', 'failed')
                  AND (delivery.next_attempt_at IS NULL OR delivery.next_attempt_at <= ?)
                ORDER BY event.snapshot_at, delivery.id LIMIT ?
                """,
                (now, limit),
            ).fetchall()
        return [
            DataHealthDelivery(
                delivery_id=int(row["delivery_id"]),
                user_id=int(row["user_id"]),
                from_status=row["from_status"],
                to_status=row["to_status"],
                payload=json.loads(row["payload"] or "{}"),
            )
            for row in rows
        ]

    def mark_data_health_sent(self, delivery_id: int, *, sent_at: datetime) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE cr_data_health_deliveries SET status='sent', attempts=attempts+1,
                    sent_at=?, next_attempt_at=NULL, last_error='' WHERE id=?
                """,
                (_utc_text(sent_at), delivery_id),
            )

    def mark_data_health_failed(
        self, delivery_id: int, *, error: str, retry_at: datetime
    ) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE cr_data_health_deliveries SET status='failed', attempts=attempts+1,
                    next_attempt_at=?, last_error=? WHERE id=?
                """,
                (_utc_text(retry_at), _sanitized_error(error), delivery_id),
            )

    def pending_alert_deliveries(self, *, limit: int = 20) -> list[AlertDelivery]:
        now = _utc_text(datetime.now(timezone.utc))
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT delivery.id AS delivery_id, delivery.user_id,
                       event.event_type, event.severity, event.from_state, event.to_state,
                       event.payload, scenario.code AS scenario_code
                FROM cr_alert_deliveries AS delivery
                JOIN cr_alert_events AS event ON event.id = delivery.event_id
                JOIN cr_scenario_definitions AS scenario ON scenario.id = event.scenario_id
                WHERE delivery.attempts < 3
                  AND delivery.status IN ('pending', 'failed')
                  AND (delivery.next_attempt_at IS NULL OR delivery.next_attempt_at <= ?)
                ORDER BY event.snapshot_at, delivery.id
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
        return [
            AlertDelivery(
                delivery_id=int(row["delivery_id"]),
                user_id=int(row["user_id"]),
                event_type=row["event_type"],
                severity=row["severity"],
                scenario_code=row["scenario_code"],
                from_state=row["from_state"],
                to_state=row["to_state"],
                payload=json.loads(row["payload"] or "{}"),
            )
            for row in rows
        ]

    def mark_alert_sent(self, delivery_id: int, *, sent_at: datetime) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE cr_alert_deliveries
                SET status = 'sent', attempts = attempts + 1, sent_at = ?,
                    next_attempt_at = NULL, last_error = ''
                WHERE id = ?
                """,
                (_utc_text(sent_at), delivery_id),
            )

    def mark_alert_failed(self, delivery_id: int, *, error: str, retry_at: datetime) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE cr_alert_deliveries
                SET status = 'failed', attempts = attempts + 1,
                    next_attempt_at = ?, last_error = ?
                WHERE id = ?
                """,
                (_utc_text(retry_at), error[:300], delivery_id),
            )

    def save_release_events(
        self,
        events,
        *,
        window_start: date | None = None,
        window_end: date | None = None,
    ) -> int:
        events = tuple(events)
        if (window_start is None) != (window_end is None):
            raise ValueError("both release-event window bounds must be provided together")
        if window_start is not None and window_end is not None and window_end < window_start:
            raise ValueError("release-event window end must not precede its start")
        if not events and window_start is None:
            return 0
        written = 0
        with self.db.connect() as connection:
            source = connection.execute("SELECT id FROM cr_sources WHERE code = 'fred'").fetchone()
            if source is None:
                raise ValueError("FRED source must be registered before saving release events")
            if window_start is not None and window_end is not None:
                connection.execute(
                    """
                    DELETE FROM cr_release_events
                    WHERE source_id = ? AND status = 'scheduled'
                      AND release_date >= ? AND release_date <= ?
                    """,
                    (source[0], window_start.isoformat(), window_end.isoformat()),
                )
            for event in events:
                payload = json.dumps(
                    event.payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                )
                cursor = connection.execute(
                    """
                    INSERT INTO cr_release_events(
                        source_id, provider_event_id, release_name, release_date,
                        scheduled_at, time_confirmed, region_code, importance,
                        status, source_url, fetched_at, payload
                    ) VALUES (?, ?, ?, ?, NULL, 0, ?, ?, 'scheduled', ?, ?, ?)
                    ON CONFLICT(source_id, provider_event_id, release_date) DO UPDATE SET
                        release_name=excluded.release_name,
                        region_code=excluded.region_code,
                        importance=excluded.importance,
                        source_url=excluded.source_url,
                        fetched_at=excluded.fetched_at,
                        payload=excluded.payload,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        source[0],
                        event.provider_event_id,
                        event.release_name,
                        event.release_date.isoformat(),
                        event.region_code,
                        event.importance,
                        event.source_url,
                        _utc_text(event.fetched_at),
                        payload,
                    ),
                )
                written += int(cursor.rowcount > 0)
        return written

    def upcoming_release_payload(
        self, *, locale: str = "ru", start_date: date, days: int = 30
    ) -> dict:
        if locale not in {"ru", "en"}:
            raise ValueError("locale must be ru or en")
        if days < 1 or days > 90:
            raise ValueError("calendar days must be between 1 and 90")
        end_date = start_date + timedelta(days=days)
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT event.provider_event_id, event.release_name, event.release_date,
                       event.scheduled_at, event.time_confirmed, event.region_code,
                       event.importance, event.status, event.source_url, event.fetched_at,
                       source.code AS source_code, source.name AS source_name
                FROM cr_release_events AS event
                JOIN cr_sources AS source ON source.id = event.source_id
                WHERE event.status = 'scheduled'
                  AND event.release_date >= ? AND event.release_date <= ?
                ORDER BY event.release_date,
                         CASE event.importance WHEN 'high' THEN 0 ELSE 1 END,
                         event.release_name
                """,
                (start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
            fetched_at = connection.execute(
                "SELECT MAX(fetched_at) FROM cr_release_events"
            ).fetchone()[0]
        return {
            "ready": fetched_at is not None,
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
            "as_of": fetched_at,
            "events": [
                {
                    **dict(row),
                    "release_name": self._localized_release_name(row["release_name"], locale),
                    "official_name": row["release_name"],
                    "time_confirmed": bool(row["time_confirmed"]),
                }
                for row in rows
            ],
        }

    @staticmethod
    def _localized_release_name(name: str, locale: str) -> str:
        if locale == "en":
            return name
        translations = {
            "consumer price index": "Индекс потребительских цен США (CPI)",
            "employment situation": "Отчёт по занятости США",
            "gross domestic product": "ВВП США",
            "federal open market committee": "Решение FOMC",
            "personal income and outlays": "Доходы, расходы и инфляция PCE США",
            "producer price index": "Индекс цен производителей США (PPI)",
            "advance monthly sales for retail and food services": "Розничные продажи США",
            "g.17 industrial production and capacity utilization": "Промышленное производство США",
            "unemployment insurance weekly claims report": "Недельные заявки на пособие по безработице США",
        }
        lowered = name.casefold()
        return translations.get(lowered, name)

    def enqueue_report_deliveries(
        self,
        *,
        report_key: str,
        report_type: str,
        report_date: date,
        payload: dict,
        user_ids: tuple[int, ...],
    ) -> int:
        if report_type not in {"midweek", "weekend"}:
            raise ValueError("unknown Crisis Radar report type")
        clean_ids = tuple(sorted({int(user_id) for user_id in user_ids if int(user_id) > 0}))
        if not clean_ids:
            return 0
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        inserted = 0
        with self.db.connect() as connection:
            for user_id in clean_ids:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO cr_report_deliveries(
                        report_key, report_type, report_date, user_id, payload
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (report_key, report_type, report_date.isoformat(), user_id, encoded),
                )
                inserted += cursor.rowcount
        return inserted

    def pending_report_deliveries(self, *, limit: int = 20) -> list[ReportDelivery]:
        now = _utc_text(datetime.now(timezone.utc))
        with self.db.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, user_id, report_type, report_date, payload
                FROM cr_report_deliveries
                WHERE attempts < 3 AND status IN ('pending', 'failed')
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY report_date, id
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
        return [
            ReportDelivery(
                delivery_id=int(row["id"]),
                user_id=int(row["user_id"]),
                report_type=row["report_type"],
                report_date=row["report_date"],
                payload=json.loads(row["payload"] or "{}"),
            )
            for row in rows
        ]

    def mark_report_sent(self, delivery_id: int, *, sent_at: datetime) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE cr_report_deliveries
                SET status='sent', attempts=attempts + 1, sent_at=?,
                    next_attempt_at=NULL, last_error=''
                WHERE id=?
                """,
                (_utc_text(sent_at), delivery_id),
            )

    def mark_report_failed(self, delivery_id: int, *, error: str, retry_at: datetime) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE cr_report_deliveries
                SET status='failed', attempts=attempts + 1, next_attempt_at=?, last_error=?
                WHERE id=?
                """,
                (_utc_text(retry_at), error[:300], delivery_id),
            )

    @staticmethod
    def _snapshot_change(connection, snapshot, *, window: timedelta) -> dict:
        cutoff = datetime.fromisoformat(snapshot["snapshot_at"]) - window
        previous = connection.execute(
            """
            SELECT * FROM cr_market_snapshots
            WHERE methodology_id = ? AND snapshot_at <= ?
            ORDER BY snapshot_at DESC, id DESC
            LIMIT 1
            """,
            (snapshot["methodology_id"], _utc_text(cutoff)),
        ).fetchone()
        if previous is None:
            return {"available": False, "window_seconds": int(window.total_seconds())}

        def states_at(snapshot_at: str) -> dict[str, dict]:
            rows = connection.execute(
                """
                SELECT indicator.code, state.value_text, state.band
                FROM cr_indicator_states AS state
                JOIN cr_indicator_definitions AS indicator ON indicator.id = state.indicator_id
                WHERE state.methodology_id = ? AND state.snapshot_at = ?
                """,
                (snapshot["methodology_id"], snapshot_at),
            ).fetchall()
            return {row["code"]: dict(row) for row in rows}

        current_states = states_at(snapshot["snapshot_at"])
        previous_states = states_at(previous["snapshot_at"])
        indicator_changes = []
        for code in sorted(current_states.keys() & previous_states.keys()):
            current_value = Decimal(current_states[code]["value_text"])
            previous_value = Decimal(previous_states[code]["value_text"])
            absolute = current_value - previous_value
            percent = None if previous_value == 0 else absolute / abs(previous_value) * 100
            indicator_changes.append(
                {
                    "code": code,
                    "from": format(previous_value, "f"),
                    "to": format(current_value, "f"),
                    "absolute": format(absolute, "f"),
                    "percent": None if percent is None else format(percent, "f"),
                    "band_from": previous_states[code]["band"],
                    "band_to": current_states[code]["band"],
                }
            )
        return {
            "available": True,
            "window_seconds": int(window.total_seconds()),
            "from": previous["snapshot_at"],
            "to": snapshot["snapshot_at"],
            "stage_from": previous["stage"],
            "stage_to": snapshot["stage"],
            "active_groups_delta": snapshot["active_group_count"] - previous["active_group_count"],
            "danger_groups_delta": snapshot["danger_group_count"] - previous["danger_group_count"],
            "critical_groups_delta": snapshot["critical_group_count"] - previous["critical_group_count"],
            "indicators": indicator_changes,
        }

    def indicator_history_payload(self, code: str, *, limit: int = 180) -> dict | None:
        if not code or len(code) > 64 or not all(char.islower() or char.isdigit() or char == "_" for char in code):
            return None
        if limit < 2 or limit > 500:
            raise ValueError("history limit must be between 2 and 500")
        with self.db.connect() as connection:
            indicator = connection.execute(
                """
                SELECT indicator.id, indicator.code, indicator.name, indicator.unit,
                       indicator.frequency, indicator.risk_direction, indicator.region_code,
                       indicator.group_code,
                       source.code AS source_code, source.name AS source_name,
                       source.base_url AS source_url,
                       thresholds.warning_value, thresholds.danger_value,
                       thresholds.critical_value, thresholds.reference_value,
                       thresholds.basis, thresholds.promotion_status,
                       thresholds.rationale_payload,
                       methodology.code AS methodology_code,
                       methodology.version AS methodology_version
                FROM cr_indicator_definitions AS indicator
                JOIN cr_sources AS source ON source.id = indicator.source_id
                JOIN cr_threshold_sets AS thresholds
                    ON thresholds.indicator_id = indicator.id
                    AND thresholds.scope = 'system' AND thresholds.owner_user_id = 0
                JOIN cr_methodology_versions AS methodology ON methodology.id = thresholds.methodology_id
                WHERE indicator.code = ? AND indicator.enabled = 1
                ORDER BY methodology.effective_from DESC, methodology.id DESC
                LIMIT 1
                """,
                (code,),
            ).fetchone()
            if indicator is None:
                return None
            rows = connection.execute(
                """
                SELECT observation.observed_at, observation.released_at,
                       observation.fetched_at, observation.value_text,
                       observation.vintage, observation.quality_flags,
                       EXISTS(
                           SELECT 1 FROM cr_observation_revisions AS revision
                           WHERE revision.revised_observation_id = observation.id
                       ) AS is_revision
                FROM cr_observations AS observation
                WHERE observation.indicator_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM cr_observations AS newer
                      WHERE newer.indicator_id = observation.indicator_id
                        AND newer.source_id = observation.source_id
                        AND newer.observed_at = observation.observed_at
                        AND (
                            newer.released_at > observation.released_at
                            OR (newer.released_at = observation.released_at AND newer.fetched_at > observation.fetched_at)
                            OR (newer.released_at = observation.released_at AND newer.fetched_at = observation.fetched_at AND newer.id > observation.id)
                        )
                  )
                ORDER BY observation.observed_at DESC
                LIMIT ?
                """,
                (indicator["id"], limit),
            ).fetchall()
        points = []
        for row in reversed(rows):
            point = dict(row)
            point["quality_flags"] = json.loads(point["quality_flags"] or "[]")
            point["is_revision"] = bool(point["is_revision"])
            points.append(point)
        return {
            "code": indicator["code"],
            "name": indicator["name"],
            "unit": indicator["unit"],
            "frequency": indicator["frequency"],
            "risk_direction": indicator["risk_direction"],
            "region_code": indicator["region_code"],
            "group_code": indicator["group_code"],
            "source": {
                "code": indicator["source_code"],
                "name": indicator["source_name"],
                "url": indicator["source_url"],
            },
            "methodology": {
                "code": indicator["methodology_code"],
                "version": indicator["methodology_version"],
            },
            "thresholds": {
                "warning": indicator["warning_value"],
                "danger": indicator["danger_value"],
                "critical": indicator["critical_value"],
                "reference": indicator["reference_value"],
            },
            "threshold_methodology": {
                "basis": indicator["basis"],
                "promotion_status": indicator["promotion_status"],
                "rationale": json.loads(indicator["rationale_payload"] or "{}"),
            },
            "points": points,
        }

    def earliest_daily_observation_values(
        self, indicator_codes: tuple[str, ...]
    ) -> dict[str, dict[date, Decimal]]:
        """Return the first stored vintage per UTC day for reproducible research."""
        if not indicator_codes or len(indicator_codes) > 32:
            raise ValueError("indicator_codes must contain between 1 and 32 values")
        if any(
            not code
            or len(code) > 64
            or not all(char.islower() or char.isdigit() or char == "_" for char in code)
            for code in indicator_codes
        ):
            raise ValueError("indicator_codes contain an invalid code")
        placeholders = ",".join("?" for _ in indicator_codes)
        with self.db.connect() as connection:
            rows = connection.execute(
                f"""
                WITH ranked AS (
                    SELECT indicator.code, substr(observation.observed_at, 1, 10) AS day,
                           observation.value_text,
                           ROW_NUMBER() OVER (
                               PARTITION BY indicator.code, substr(observation.observed_at, 1, 10)
                               ORDER BY observation.released_at ASC,
                                        observation.fetched_at ASC,
                                        observation.id ASC
                           ) AS rank
                    FROM cr_observations AS observation
                    JOIN cr_indicator_definitions AS indicator
                      ON indicator.id = observation.indicator_id
                    WHERE indicator.code IN ({placeholders})
                )
                SELECT code, day, value_text FROM ranked
                WHERE rank = 1 ORDER BY day, code
                """,
                indicator_codes,
            ).fetchall()
        result = {code: {} for code in indicator_codes}
        for row in rows:
            result[row["code"]][date.fromisoformat(row["day"])] = Decimal(row["value_text"])
        return result

    def regional_contour_payload(
        self,
        *,
        methodology_code: str,
        methodology_version: str,
        locale: str = "ru",
        as_of: datetime | None = None,
    ) -> dict:
        """Return the latest persisted world contour without deriving missing values."""
        if locale not in {"ru", "en"}:
            raise ValueError("locale must be ru or en")
        now = as_of or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        as_of_text = _utc_text(now)
        with self.db.connect() as connection:
            methodology = connection.execute(
                """
                SELECT id, code, version
                FROM cr_methodology_versions
                WHERE code = ? AND version = ?
                LIMIT 1
                """,
                (methodology_code, methodology_version),
            ).fetchone()
            if methodology is None:
                return {
                    "ready": False,
                    "as_of": as_of_text,
                    "snapshot_at": None,
                    "stage": None,
                    "methodology": None,
                    "regions": [
                        {
                            "code": code,
                            "name": _REGION_NAMES[code][locale],
                            "ready": False,
                            "indicator_count": 0,
                            "available_count": 0,
                            "missing_count": 0,
                            "freshness": {value.value: 0 for value in DataFreshness},
                            "bands": {band.value: 0 for band in IndicatorBand},
                            "groups": [],
                            "indicators": [],
                        }
                        for code in _REGION_CODES
                    ],
                }
            snapshot = connection.execute(
                """
                SELECT id, snapshot_at, stage
                FROM cr_market_snapshots
                WHERE methodology_id = ? AND snapshot_at <= ?
                ORDER BY snapshot_at DESC, id DESC
                LIMIT 1
                """,
                (methodology["id"], as_of_text),
            ).fetchone()
            snapshot_at = None if snapshot is None else snapshot["snapshot_at"]
            rows = connection.execute(
                """
                SELECT indicator.id AS indicator_id, indicator.code, indicator.name,
                       indicator.group_code, indicator.region_code, indicator.unit,
                       indicator.frequency, indicator.risk_direction,
                       indicator.provider_series_id, indicator.transform,
                       indicator.max_staleness_seconds,
                       source.code AS source_code, source.name AS source_name,
                       source.base_url, source.terms_url,
                       observation.id AS observation_id, observation.value_text,
                       observation.observed_at, observation.released_at,
                       observation.fetched_at, observation.vintage,
                       observation.quality_flags, observation.content_hash,
                       state.observation_id AS state_observation_id,
                       state.snapshot_at AS state_snapshot_at, state.band,
                       state.raw_band, state.stress_score_text,
                       state.distance_to_next_text, state.persistence_count,
                       state.confirmation_required, state.held_by_hysteresis,
                       state.quality_score_text,
                       thresholds.warning_value, thresholds.danger_value,
                       thresholds.critical_value, thresholds.reference_value
                FROM cr_indicator_definitions AS indicator
                JOIN cr_sources AS source ON source.id = indicator.source_id
                JOIN cr_threshold_sets AS thresholds
                    ON thresholds.indicator_id = indicator.id
                   AND thresholds.methodology_id = ?
                   AND thresholds.scope = 'system' AND thresholds.owner_user_id = 0
                LEFT JOIN cr_observations AS observation ON observation.id = (
                    SELECT latest.id
                    FROM cr_observations AS latest
                    WHERE latest.indicator_id = indicator.id
                      AND latest.released_at <= ?
                    ORDER BY latest.observed_at DESC, latest.released_at DESC,
                             latest.fetched_at DESC, latest.id DESC
                    LIMIT 1
                )
                LEFT JOIN cr_indicator_states AS state
                    ON state.indicator_id = indicator.id
                   AND state.methodology_id = ?
                   AND state.snapshot_at = ?
                WHERE indicator.enabled = 1
                ORDER BY indicator.code
                """,
                (methodology["id"], as_of_text, methodology["id"], snapshot_at),
            ).fetchall()
            group_rows = (
                []
                if snapshot_at is None
                else connection.execute(
                    """
                    SELECT group_code, band, stress_score_text, indicator_count,
                           worsening_count, contributors_payload
                    FROM cr_group_states
                    WHERE methodology_id = ? AND snapshot_at = ?
                    ORDER BY group_code
                    """,
                    (methodology["id"], snapshot_at),
                ).fetchall()
            )

        group_states = {row["group_code"]: row for row in group_rows}
        region_indicators: dict[str, list[dict]] = {code: [] for code in _REGION_CODES}
        for row in rows:
            region = _normalized_region(row["region_code"])
            freshness, age_seconds = _freshness_from_release(
                row["released_at"],
                as_of=now,
                max_staleness_seconds=int(row["max_staleness_seconds"]),
            )
            state_matches = (
                row["observation_id"] is not None
                and row["state_observation_id"] == row["observation_id"]
            )
            region_indicators[region].append(
                {
                    "code": row["code"],
                    "name": row["name"],
                    "group_code": row["group_code"],
                    "provider_region_code": row["region_code"],
                    "value_text": row["value_text"],
                    "unit": row["unit"],
                    "frequency": row["frequency"],
                    "risk_direction": row["risk_direction"],
                    "freshness": freshness,
                    "age_seconds": age_seconds,
                    "observed_at": row["observed_at"],
                    "released_at": row["released_at"],
                    "fetched_at": row["fetched_at"],
                    "band": row["band"] if state_matches else None,
                    "raw_band": row["raw_band"] if state_matches else None,
                    "stress_score": row["stress_score_text"] if state_matches else None,
                    "distance_to_next": row["distance_to_next_text"] if state_matches else None,
                    "persistence_count": row["persistence_count"] if state_matches else None,
                    "confirmation_required": row["confirmation_required"] if state_matches else None,
                    "held_by_hysteresis": (
                        bool(row["held_by_hysteresis"]) if state_matches else None
                    ),
                    "quality_score": row["quality_score_text"] if state_matches else None,
                    "analysis_pending": bool(row["observation_id"] is not None and not state_matches),
                    "thresholds": {
                        "warning": row["warning_value"],
                        "danger": row["danger_value"],
                        "critical": row["critical_value"],
                        "reference": row["reference_value"],
                    },
                    "provenance": {
                        "observation_id": row["observation_id"],
                        "source": {
                            "code": row["source_code"],
                            "name": row["source_name"],
                            "base_url": row["base_url"],
                            "terms_url": row["terms_url"],
                        },
                        "provider_series_id": row["provider_series_id"],
                        "transform": row["transform"],
                        "vintage": row["vintage"],
                        "content_hash": row["content_hash"],
                        "quality_flags": (
                            json.loads(row["quality_flags"] or "[]")
                            if row["observation_id"] is not None
                            else []
                        ),
                        "state_snapshot_at": row["state_snapshot_at"] if state_matches else None,
                    },
                }
            )

        regions = []
        for region_code in _REGION_CODES:
            indicators = region_indicators[region_code]
            grouped: dict[str, list[dict]] = {}
            for indicator in indicators:
                grouped.setdefault(indicator["group_code"], []).append(indicator)
            groups = []
            for group_code, members in sorted(grouped.items()):
                saved = group_states.get(group_code)
                groups.append(
                    {
                        "code": group_code,
                        "name": _GROUP_NAMES.get(group_code, {}).get(locale, group_code),
                        "band": None if saved is None else saved["band"],
                        "stress_score": None if saved is None else saved["stress_score_text"],
                        "aggregate_scope": "methodology_global" if saved is not None else None,
                        "indicator_count": len(members),
                        "available_count": sum(item["value_text"] is not None for item in members),
                        "missing_count": sum(item["value_text"] is None for item in members),
                        "worsening_count": None if saved is None else saved["worsening_count"],
                        "contributors": (
                            [] if saved is None else json.loads(saved["contributors_payload"] or "[]")
                        ),
                        "indicator_codes": [item["code"] for item in members],
                    }
                )
            freshness_counts = {
                value.value: sum(item["freshness"] == value.value for item in indicators)
                for value in DataFreshness
            }
            band_counts = {
                band.value: sum(item["band"] == band.value for item in indicators)
                for band in IndicatorBand
            }
            available_count = sum(item["value_text"] is not None for item in indicators)
            regions.append(
                {
                    "code": region_code,
                    "name": _REGION_NAMES[region_code][locale],
                    "ready": available_count > 0,
                    "indicator_count": len(indicators),
                    "available_count": available_count,
                    "missing_count": len(indicators) - available_count,
                    "freshness": freshness_counts,
                    "bands": band_counts,
                    "groups": groups,
                    "indicators": indicators,
                }
            )
        return {
            "ready": any(region["ready"] for region in regions),
            "as_of": as_of_text,
            "snapshot_at": snapshot_at,
            "stage": None if snapshot is None else snapshot["stage"],
            "methodology": {"code": methodology["code"], "version": methodology["version"]},
            "regions": regions,
        }

    def source_health_payload(self, *, as_of: datetime | None = None) -> dict:
        """Return sanitized ingestion health and real observation coverage per source."""
        now = as_of or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        with self.db.connect() as connection:
            sources = connection.execute("SELECT * FROM cr_sources ORDER BY code").fetchall()
            payloads = []
            for source in sources:
                latest = connection.execute(
                    """
                    SELECT * FROM cr_sync_runs
                    WHERE source_id = ? ORDER BY started_at DESC, id DESC LIMIT 1
                    """,
                    (source["id"],),
                ).fetchone()
                last_success = connection.execute(
                    """
                    SELECT * FROM cr_sync_runs
                    WHERE source_id = ? AND status = 'succeeded'
                    ORDER BY finished_at DESC, id DESC LIMIT 1
                    """,
                    (source["id"],),
                ).fetchone()
                last_failure = connection.execute(
                    """
                    SELECT * FROM cr_sync_runs
                    WHERE source_id = ? AND status = 'failed'
                    ORDER BY finished_at DESC, id DESC LIMIT 1
                    """,
                    (source["id"],),
                ).fetchone()
                totals = connection.execute(
                    """
                    SELECT count(*) AS run_count, coalesce(sum(rows_fetched), 0) AS rows_fetched,
                           coalesce(sum(rows_written), 0) AS rows_written
                    FROM cr_sync_runs WHERE source_id = ?
                    """,
                    (source["id"],),
                ).fetchone()
                indicators = connection.execute(
                    """
                    SELECT indicator.id, indicator.max_staleness_seconds,
                           observation.released_at
                    FROM cr_indicator_definitions AS indicator
                    LEFT JOIN cr_observations AS observation ON observation.id = (
                        SELECT latest_observation.id
                        FROM cr_observations AS latest_observation
                        WHERE latest_observation.indicator_id = indicator.id
                          AND latest_observation.released_at <= ?
                        ORDER BY latest_observation.observed_at DESC,
                                 latest_observation.released_at DESC,
                                 latest_observation.fetched_at DESC,
                                 latest_observation.id DESC
                        LIMIT 1
                    )
                    WHERE indicator.source_id = ? AND indicator.enabled = 1
                    ORDER BY indicator.code
                    """,
                    (_utc_text(now), source["id"]),
                ).fetchall()

                def run_payload(run) -> dict | None:
                    if run is None:
                        return None
                    return {
                        "id": int(run["id"]),
                        "status": run["status"],
                        "started_at": run["started_at"],
                        "finished_at": run["finished_at"],
                        "rows_fetched": int(run["rows_fetched"]),
                        "rows_written": int(run["rows_written"]),
                        "error": {
                            "code": run["error_code"],
                            "detail": _sanitized_error(run["error_detail"]),
                        }
                        if run["error_code"] or run["error_detail"]
                        else None,
                    }

                freshness_counts = {value.value: 0 for value in DataFreshness}
                for indicator in indicators:
                    freshness, _ = _freshness_from_release(
                        indicator["released_at"],
                        as_of=now,
                        max_staleness_seconds=int(indicator["max_staleness_seconds"]),
                    )
                    freshness_counts[freshness] += 1
                observed_count = len(indicators) - freshness_counts[DataFreshness.MISSING.value]
                sync_age_seconds = None
                sync_freshness = DataFreshness.MISSING.value
                sync_anchor = (
                    latest
                    if latest is not None and latest["status"] in {"succeeded", "partial"}
                    else last_success
                )
                if sync_anchor is not None and sync_anchor["finished_at"] is not None:
                    sync_freshness, sync_age_seconds = _freshness_from_release(
                        sync_anchor["finished_at"],
                        as_of=now,
                        max_staleness_seconds=int(source["max_staleness_seconds"]),
                    )
                if not bool(source["enabled"]):
                    status = "disabled"
                elif latest is None:
                    status = "never_synced"
                elif latest["status"] == "running":
                    status = "running"
                elif latest["status"] == "failed":
                    status = "failed" if last_success is None else "degraded"
                elif latest["status"] == "partial":
                    status = "degraded"
                elif sync_freshness == DataFreshness.STALE.value:
                    status = "stale"
                else:
                    status = "healthy"
                payloads.append(
                    {
                        "code": source["code"],
                        "name": source["name"],
                        "status": status,
                        "enabled": bool(source["enabled"]),
                        "access_type": source["access_type"],
                        "expected_frequency": source["expected_frequency"],
                        "max_staleness_seconds": int(source["max_staleness_seconds"]),
                        "base_url": source["base_url"],
                        "terms_url": source["terms_url"],
                        "sync_freshness": sync_freshness,
                        "sync_age_seconds": sync_age_seconds,
                        "latest_run": run_payload(latest),
                        "last_success": run_payload(last_success),
                        "last_failure": run_payload(last_failure),
                        "rows": {
                            "run_count": int(totals["run_count"]),
                            "fetched_total": int(totals["rows_fetched"]),
                            "written_total": int(totals["rows_written"]),
                        },
                        "coverage": {
                            "indicator_count": len(indicators),
                            "observed_count": observed_count,
                            "ratio": (
                                None
                                if not indicators
                                else format(Decimal(observed_count) / Decimal(len(indicators)), ".4f")
                            ),
                            "freshness": freshness_counts,
                        },
                    }
                )
        statuses: dict[str, int] = {}
        for item in payloads:
            statuses[item["status"]] = statuses.get(item["status"], 0) + 1
        return {
            "ready": any(item["status"] in {"healthy", "degraded", "stale"} for item in payloads),
            "as_of": _utc_text(now),
            "summary": {"source_count": len(payloads), "statuses": statuses},
            "sources": payloads,
        }

    def latest_overview_payload(
        self,
        *,
        methodology_code: str,
        methodology_version: str,
        locale: str = "ru",
        owner_user_id: int = 0,
    ) -> dict:
        if locale not in {"ru", "en"}:
            raise ValueError("locale must be ru or en")
        with self.db.connect() as connection:
            snapshot = connection.execute(
                """
                SELECT market.*, methodology.code AS methodology_code,
                       methodology.version AS methodology_version
                FROM cr_market_snapshots AS market
                JOIN cr_methodology_versions AS methodology ON methodology.id = market.methodology_id
                WHERE methodology.code = ? AND methodology.version = ?
                ORDER BY market.snapshot_at DESC, market.id DESC
                LIMIT 1
                """,
                (methodology_code, methodology_version),
            ).fetchone()
            if snapshot is None:
                source_rows = connection.execute(
                    """
                    SELECT source.code, source.name, source.enabled,
                           latest.status, latest.finished_at, latest.error_code
                    FROM cr_sources AS source
                    LEFT JOIN cr_sync_runs AS latest ON latest.id = (
                        SELECT run.id FROM cr_sync_runs AS run
                        WHERE run.source_id = source.id ORDER BY run.started_at DESC, run.id DESC LIMIT 1
                    )
                    ORDER BY source.code
                    """
                ).fetchall()
                return {
                    "ready": False,
                    "stage": "unknown",
                    "as_of": None,
                    "explanation": "Данные ещё не загружены. Запустите синхронизацию."
                    if locale == "ru"
                    else "Data has not been loaded yet. Run synchronization.",
                    "groups": [],
                    "scenarios": [],
                    "indicators": [],
                    "changes": {
                        "24h": {"available": False, "window_seconds": 86400},
                        "7d": {"available": False, "window_seconds": 604800},
                        "15d": {"available": False, "window_seconds": 1296000},
                    },
                    "sources": [dict(row) for row in source_rows],
                    "methodology": {
                        "code": methodology_code,
                        "version": methodology_version,
                    },
                }
            group_rows = connection.execute(
                """
                SELECT group_code, band, stress_score_text, indicator_count,
                       worsening_count, contributors_payload
                FROM cr_group_states
                WHERE methodology_id = ? AND snapshot_at = ?
                ORDER BY stress_score_text DESC, group_code
                """,
                (snapshot["methodology_id"], snapshot["snapshot_at"]),
            ).fetchall()
            scenario_rows = connection.execute(
                """
                SELECT definition.code, definition.name_payload, definition.horizon,
                       state.status, state.confidence, state.active_group_count,
                       state.evidence_payload, state.explanation_payload
                FROM cr_scenario_states AS state
                JOIN cr_scenario_definitions AS definition ON definition.id = state.scenario_id
                WHERE state.methodology_id = ? AND state.snapshot_at = ?
                ORDER BY CASE state.status
                    WHEN 'confirmed' THEN 3 WHEN 'elevated' THEN 2
                    WHEN 'watch' THEN 1 ELSE 0 END DESC, definition.code
                """,
                (snapshot["methodology_id"], snapshot["snapshot_at"]),
            ).fetchall()
            indicator_rows = connection.execute(
                """
                SELECT indicator.code, indicator.name, indicator.group_code, indicator.region_code,
                       state.value_text, indicator.unit, state.band, state.distance_to_next_text,
                       state.stress_score_text, state.freshness, state.quality_score_text,
                       state.persistence_count, state.raw_band, state.held_by_hysteresis,
                       state.confirmation_required,
                       observation.observed_at, observation.released_at, observation.fetched_at,
                       source.code AS source_code, source.name AS source_name,
                       source.base_url AS source_url, source.terms_url,
                       thresholds.warning_value, thresholds.danger_value, thresholds.critical_value,
                       thresholds.reference_value, thresholds.basis,
                       thresholds.promotion_status, thresholds.rationale_payload
                FROM cr_indicator_states AS state
                JOIN cr_indicator_definitions AS indicator ON indicator.id = state.indicator_id
                JOIN cr_observations AS observation ON observation.id = state.observation_id
                JOIN cr_sources AS source ON source.id = observation.source_id
                JOIN cr_threshold_sets AS thresholds
                    ON thresholds.indicator_id = indicator.id
                    AND thresholds.methodology_id = state.methodology_id
                    AND thresholds.scope = 'system' AND thresholds.owner_user_id = 0
                WHERE state.methodology_id = ? AND state.snapshot_at = ?
                ORDER BY state.stress_score_text DESC, indicator.code
                """,
                (snapshot["methodology_id"], snapshot["snapshot_at"]),
            ).fetchall()
            source_rows = connection.execute(
                """
                SELECT source.code, source.name, source.enabled,
                       latest.status, latest.started_at, latest.finished_at,
                       latest.rows_fetched, latest.rows_written, latest.error_code
                FROM cr_sources AS source
                LEFT JOIN cr_sync_runs AS latest ON latest.id = (
                    SELECT run.id FROM cr_sync_runs AS run
                    WHERE run.source_id = source.id ORDER BY run.started_at DESC, run.id DESC LIMIT 1
                )
                ORDER BY source.code
                """
            ).fetchall()
            changes = {
                "24h": self._snapshot_change(connection, snapshot, window=timedelta(hours=24)),
                "7d": self._snapshot_change(connection, snapshot, window=timedelta(days=7)),
                "15d": self._snapshot_change(connection, snapshot, window=timedelta(days=15)),
            }
            personal_rows = (
                []
                if owner_user_id <= 0
                else connection.execute(
                    """
                    SELECT indicator.code, thresholds.warning_value,
                           thresholds.danger_value, thresholds.critical_value,
                           thresholds.reference_value
                    FROM cr_threshold_sets AS thresholds
                    JOIN cr_indicator_definitions AS indicator
                      ON indicator.id = thresholds.indicator_id
                    WHERE thresholds.methodology_id = ?
                      AND thresholds.scope = 'personal'
                      AND thresholds.owner_user_id = ?
                    """,
                    (snapshot["methodology_id"], owner_user_id),
                ).fetchall()
            )
        explanation = json.loads(snapshot["explanation_payload"] or "{}")
        indicators = []
        personal_by_code = {row["code"]: row for row in personal_rows}
        for row in indicator_rows:
            payload = dict(row)
            warning = payload.pop("warning_value")
            danger = payload.pop("danger_value")
            critical = payload.pop("critical_value")
            reference = payload.pop("reference_value")
            basis = payload.pop("basis")
            promotion_status = payload.pop("promotion_status")
            rationale = json.loads(payload.pop("rationale_payload") or "{}")
            payload["thresholds"] = {
                "warning": warning,
                "danger": danger,
                "critical": critical,
                "reference": reference,
                "basis": basis,
                "promotion_status": promotion_status,
                "rationale": rationale,
            }
            personal = personal_by_code.get(payload["code"])
            payload["personal_thresholds"] = (
                None
                if personal is None
                else {
                    "warning": personal["warning_value"],
                    "danger": personal["danger_value"],
                    "critical": personal["critical_value"],
                    "reference": personal["reference_value"],
                }
            )
            payload["held_by_hysteresis"] = bool(payload["held_by_hysteresis"])
            indicators.append(payload)

        coverage_details = json.loads(snapshot["coverage_payload"] or "{}")
        return {
            "ready": True,
            "stage": snapshot["stage"],
            "calculated_stage": snapshot["calculated_stage"],
            "as_of": snapshot["snapshot_at"],
            "dominant_window": snapshot["dominant_window"],
            "explanation": explanation.get(locale, ""),
            "breadth": {
                "active": snapshot["active_group_count"],
                "warning_or_worse": snapshot["warning_group_count"],
                "danger_or_worse": snapshot["danger_group_count"],
                "critical": snapshot["critical_group_count"],
            },
            "methodology": {
                "code": snapshot["methodology_code"],
                "version": snapshot["methodology_version"],
            },
            "coverage": {
                "enabled": snapshot["coverage_status"] != "not_evaluated",
                "status": snapshot["coverage_status"],
                "ratio": snapshot["coverage_ratio_text"],
                **coverage_details,
            },
            "changes": changes,
            "groups": [
                {
                    "code": row["group_code"],
                    "band": row["band"],
                    "stress_score": row["stress_score_text"],
                    "indicator_count": row["indicator_count"],
                    "worsening_count": row["worsening_count"],
                    "contributors": json.loads(row["contributors_payload"] or "[]"),
                }
                for row in group_rows
            ],
            "scenarios": [
                {
                    "code": row["code"],
                    "name": json.loads(row["name_payload"] or "{}").get(locale, row["code"]),
                    "horizon": row["horizon"],
                    "status": row["status"],
                    "confidence": row["confidence"],
                    "active_group_count": row["active_group_count"],
                    "evidence": json.loads(row["evidence_payload"] or "[]"),
                    "explanation": json.loads(row["explanation_payload"] or "{}").get(locale, ""),
                }
                for row in scenario_rows
            ],
            "indicators": indicators,
            "sources": [dict(row) for row in source_rows],
        }
