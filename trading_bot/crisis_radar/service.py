from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from trading_bot.crisis_radar.catalog import (
    BYBIT_INDICATORS,
    BYBIT_RESEARCH_INDICATORS,
    BYBIT_SIGNED_V11_INDICATORS,
    FRED_INDICATORS,
    FRED_GLOBAL_V2_INDICATORS,
    FRED_HISTORICAL_BACKFILL_MODES,
    FRED_V11_DEPTH_INDICATORS,
    FRED_V12_RESEARCH_INDICATORS,
    METHODOLOGY_CODE,
    METHODOLOGY_GLOBAL_V2_VERSION,
    METHODOLOGY_VERSION,
    METHODOLOGY_V2_VERSION,
    METHODOLOGY_V11_VERSION,
    STARTER_INDICATORS,
    GLOBAL_V2_INDICATORS,
    V11_INDICATORS,
    V11_SCENARIOS,
    V2_INDICATORS,
    bootstrap_global_v2_catalog,
    bootstrap_starter_catalog,
    bootstrap_v2_catalog,
    bootstrap_v11_catalog,
    bootstrap_v14_catalog,
)
from trading_bot.crisis_radar.coverage import (
    DEFAULT_REQUIRED_REGIONS,
    GLOBAL_V2_REQUIRED_REGIONS,
    V11_REQUIRED_GROUPS,
    ExpectedIndicator,
    assess_coverage,
)
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.news import (
    NEWS_RULE_VERSION,
    classify_news,
    normalize_official_news,
)
from trading_bot.crisis_radar.event_pipeline import extract_event_candidate
from trading_bot.crisis_radar.evidence_pipeline import EvidencePipeline
from trading_bot.crisis_radar.official_catalogs import bootstrap_official_event_catalogs
from trading_bot.crisis_radar.domain import MarketOverview, QualityFlag
from trading_bot.crisis_radar.derived_labels import (
    CryptoDailyRecord,
    generate_crypto_leverage_unwind_labels,
)
from trading_bot.crisis_radar.event_catalog import EventCatalogVersion
from trading_bot.crisis_radar.exposure import build_exposure_overlay
from trading_bot.crisis_radar.opportunities import (
    MarketQuote,
    MarketStage as OpportunityMarketStage,
    OpportunityContext,
    ScenarioSignal,
    generate_opportunities,
)
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.scenarios import SCENARIOS, V2_SCENARIOS, build_scenario_states
from trading_bot.crisis_radar.scenario_fusion import fuse_scenarios
from trading_bot.crisis_radar.scoring_v2 import score_indicator_v2
from trading_bot.crisis_radar.scenario_v2 import calculate_scenario_v2
from trading_bot.crisis_radar.sources.base import SeriesRequest
from trading_bot.crisis_radar.sources.base import SourcePayloadError
from trading_bot.crisis_radar.sources.bea import BeaAdapter
from trading_bot.crisis_radar.sources.bybit import BybitAdapter, BybitClient, BybitSourceError
from trading_bot.crisis_radar.sources.eia import EiaAdapter
from trading_bot.crisis_radar.sources.ecb import EcbAdapter
from trading_bot.crisis_radar.sources.eurostat import EurostatAdapter
from trading_bot.crisis_radar.sources.europe_clients import EcbClient, EuropeSourceError, EurostatClient
from trading_bot.crisis_radar.sources.fred import FredAdapter, FredTransformAdapter
from trading_bot.crisis_radar.sources.fred_calendar import FredCalendarAdapter
from trading_bot.crisis_radar.sources.fred_client import FredClient, FredClientError
from trading_bot.crisis_radar.sources.global_clients import (
    BisClient,
    GlobalSourceError,
    OecdClient,
    WorldBankClient,
)
from trading_bot.crisis_radar.sources.global_data import BisAdapter, OecdAdapter, WorldBankAdapter
from trading_bot.crisis_radar.sources.gdelt import GdeltDiscoveryAdapter
from trading_bot.crisis_radar.sources.news_clients import (
    GdeltDiscoveryClient,
    NewsClient,
    NewsSourceError,
)
from trading_bot.crisis_radar.sources.official_clients import BeaClient, EiaClient, OfficialSourceError
from trading_bot.crisis_radar.stability import STABILITY_POLICY, stabilize_indicator_state
from trading_bot.crisis_radar.states import build_indicator_state, build_market_overview
from trading_bot.crisis_radar.stage_v2 import calculate_stage_v2, dependency_for
from trading_bot.crisis_radar.trends import calculate_contagion, calculate_indicator_features
from trading_bot.crisis_radar.validation import evaluate_stored_calibration_gate


def _active_fred_collection_seeds(
    feature_flags: CrisisRadarFeatureFlags,
) -> tuple:
    seeds = FRED_INDICATORS
    if feature_flags.global_sources_v2 or feature_flags.scoring_v11:
        seeds += FRED_GLOBAL_V2_INDICATORS
    if feature_flags.scoring_v11:
        seeds += FRED_V11_DEPTH_INDICATORS + FRED_V12_RESEARCH_INDICATORS
    return seeds


_ALL_FRED_COLLECTION_SEEDS = (
    FRED_INDICATORS
    + FRED_GLOBAL_V2_INDICATORS
    + FRED_V11_DEPTH_INDICATORS
    + FRED_V12_RESEARCH_INDICATORS
)


class CrisisRadarService:
    def __init__(
        self,
        repository: CrisisRadarRepository,
        *,
        feature_flags: CrisisRadarFeatureFlags | None = None,
        evidence_pipeline: EvidencePipeline | None = None,
    ) -> None:
        self.repository = repository
        self.feature_flags = feature_flags or CrisisRadarFeatureFlags.from_environment()
        self.evidence_pipeline = evidence_pipeline
        if self.feature_flags.global_sources_v2:
            self.methodology_version = METHODOLOGY_GLOBAL_V2_VERSION
            self.indicators = GLOBAL_V2_INDICATORS
            self.scenarios = V2_SCENARIOS
        elif self.feature_flags.thresholds_v2:
            self.methodology_version = METHODOLOGY_V2_VERSION
            self.indicators = V2_INDICATORS
            self.scenarios = SCENARIOS
        else:
            self.methodology_version = METHODOLOGY_VERSION
            self.indicators = STARTER_INDICATORS
            self.scenarios = SCENARIOS

    def bootstrap(self) -> dict[str, int | str]:
        result = bootstrap_starter_catalog(self.repository)
        if self.feature_flags.global_sources_v2:
            result = bootstrap_global_v2_catalog(self.repository)
        elif self.feature_flags.thresholds_v2:
            result = bootstrap_v2_catalog(self.repository)
        result["event_catalog_count"] = len(bootstrap_official_event_catalogs(self.repository))
        if self.feature_flags.scoring_v11:
            result["shadow_v11"] = bootstrap_v11_catalog(self.repository)
            result["research_v14"] = bootstrap_v14_catalog(self.repository)
        return result

    def derive_crypto_event_catalog(
        self, *, effective_at: datetime | None = None
    ) -> dict[str, int | str | None]:
        """Create a versioned, explicitly non-official catalog from stored Bybit history."""
        self.bootstrap()
        now = effective_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("effective_at must be timezone-aware")
        codes = (
            "btc_close_price",
            "btc_return_7d",
            "btc_open_interest",
            "btc_oi_7d_change",
            "btc_funding_rate",
            "eth_return_7d",
        )
        series = self.repository.earliest_daily_observation_values(codes)
        days = sorted(series["btc_close_price"])
        records = [
            CryptoDailyRecord(
                observed_at=datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
                btc_price=series["btc_close_price"].get(day),
                btc_return_7d=series["btc_return_7d"].get(day),
                oi_level=series["btc_open_interest"].get(day),
                oi_change_7d=series["btc_oi_7d_change"].get(day),
                funding=series["btc_funding_rate"].get(day),
                eth_breadth=series["eth_return_7d"].get(day),
            )
            for day in days
        ]
        result = generate_crypto_leverage_unwind_labels(records)
        version = f"bybit-derived-v1-{now:%Y%m%d}-{result.input_checksum[:12]}"
        catalog = EventCatalogVersion(
            scenario_code="crypto_leverage_unwind",
            version=version,
            source_name="Bybit public market data (derived research rule)",
            source_url=result.definition.get("source_url", "")
            or "https://bybit-exchange.github.io/docs/v5/market/open-interest",
            definition={
                **result.definition,
                "input_checksum": result.input_checksum,
                "result_checksum": result.checksum,
                "record_count": len(records),
                "coverage_start": None if not days else days[0].isoformat(),
                "coverage_end": None if not days else days[-1].isoformat(),
            },
            limitations=(
                "Labels are derived from a frozen market-data rule and are not official crisis declarations.",
                "Exchange history and endpoint retention bound the number of independent events.",
                "The catalog is eligible for research replay only; probability gates still apply.",
            ),
            effective_from=now,
            labels=result.labels,
        )
        catalog_id = self.repository.register_event_catalog(catalog)
        sufficient = sum(item.sufficient for item in result.evaluations)
        return {
            "catalog_id": catalog_id,
            "version": version,
            "input_checksum": result.input_checksum,
            "result_checksum": result.checksum,
            "record_count": len(records),
            "sufficient_count": sufficient,
            "label_count": len(result.labels),
            "coverage_start": None if not days else days[0].isoformat(),
            "coverage_end": None if not days else days[-1].isoformat(),
        }

    async def sync_fred(
        self,
        client: FredClient,
        *,
        fetched_at: datetime | None = None,
        recompute_after: bool = True,
    ) -> dict[str, int | str | None | list[str]]:
        self.bootstrap()
        now = fetched_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        sync_run_id = self.repository.start_sync_run("fred", started_at=now)
        adapter = FredAdapter()
        transform_adapter = FredTransformAdapter()
        rows_fetched = 0
        required_rows_fetched = 0
        rows_written = 0
        required_errors: list[str] = []
        research_errors: list[str] = []
        fred_seeds = _active_fred_collection_seeds(self.feature_flags)
        research_codes = {item.code for item in FRED_V12_RESEARCH_INDICATORS}
        for seed in fred_seeds:
            request = SeriesRequest(seed.code, seed.provider_series_id, seed.unit)
            try:
                payload = (
                    await client.fetch(request)
                    if seed.transform == "identity"
                    else await client.fetch(request, limit=140)
                )
                observations = (
                    adapter.normalize(payload, request, fetched_at=now)
                    if seed.transform == "identity"
                    else transform_adapter.normalize(
                        payload,
                        request,
                        transform=seed.transform,
                        fetched_at=now,
                    )
                )
                rows_fetched += len(observations)
                if seed.code not in research_codes:
                    required_rows_fetched += len(observations)
                for observation in observations:
                    result = self.repository.save_observation(observation, sync_run_id=sync_run_id)
                    rows_written += int(result.inserted)
            except (FredClientError, SourcePayloadError) as exc:
                target = research_errors if seed.code in research_codes else required_errors
                target.append(f"{seed.code}:{type(exc).__name__}")
        required_seed_count = sum(seed.code not in research_codes for seed in fred_seeds)
        status = (
            "failed"
            if len(required_errors) == required_seed_count
            else "partial"
            if required_errors
            else "succeeded"
        )
        self.repository.finish_sync_run(
            sync_run_id,
            finished_at=datetime.now(timezone.utc),
            status=status,
            rows_fetched=rows_fetched,
            rows_written=rows_written,
            error_code="source_errors" if required_errors else "",
            error_detail=",".join(required_errors),
        )
        overview = (
            self.recompute(snapshot_at=now)
            if required_rows_fetched and recompute_after
            else None
        )
        result: dict[str, int | str | None | list[str]] = {
            "sync_run_id": sync_run_id,
            "status": status,
            "rows_fetched": rows_fetched,
            "rows_written": rows_written,
            "stage": None if overview is None else overview.stage.value,
        }
        if research_codes & {seed.code for seed in fred_seeds}:
            result["research_errors"] = research_errors
        return result

    async def backfill_fred(
        self,
        client: FredClient,
        *,
        started_on: date,
        ended_on: date,
        fetched_at: datetime | None = None,
        recompute_after: bool = True,
        indicator_codes: set[str] | None = None,
    ) -> dict[str, int | str | None]:
        self.bootstrap()
        if ended_on < started_on:
            raise ValueError("FRED backfill end date must not precede start date")
        if (ended_on - started_on).days > 365 * 50 + 20:
            raise ValueError("FRED backfill window must not exceed 50 years")
        now = fetched_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        if ended_on > now.date():
            raise ValueError("FRED backfill cannot request future dates")
        sync_run_id = self.repository.start_sync_run("fred", started_at=now)
        adapter = FredAdapter()
        transform_adapter = FredTransformAdapter()
        rows_fetched = 0
        rows_written = 0
        errors: list[str] = []
        error_details: list[str] = []
        skipped: list[str] = []
        active_seeds = _active_fred_collection_seeds(self.feature_flags)
        known_codes = {item.code for item in _ALL_FRED_COLLECTION_SEEDS}
        if indicator_codes is not None and (not indicator_codes or not indicator_codes <= known_codes):
            raise ValueError("indicator_codes must contain known FRED indicator codes")
        selected_seeds = tuple(
            item
            for item in (
                active_seeds if indicator_codes is None else _ALL_FRED_COLLECTION_SEEDS
            )
            if indicator_codes is None or item.code in indicator_codes
        )
        for seed in selected_seeds:
            request = SeriesRequest(seed.code, seed.provider_series_id, seed.unit)
            try:
                backfill_mode = FRED_HISTORICAL_BACKFILL_MODES.get(
                    seed.code,
                    getattr(seed, "historical_backfill_mode", "initial_release"),
                )
                if backfill_mode not in {
                    "initial_release",
                    "current_revision_research",
                    "live_only",
                }:
                    raise RuntimeError("unsupported FRED historical backfill mode")
                if backfill_mode == "live_only":
                    skipped.append(f"{seed.code}:live_only")
                    continue
                initial_release = backfill_mode == "initial_release"
                payload = await client.fetch_history(
                    request,
                    observation_start=started_on,
                    observation_end=ended_on,
                    initial_release=initial_release,
                )
                observations = (
                    adapter.normalize(
                        payload,
                        request,
                        fetched_at=now,
                        release_from_vintage=initial_release,
                    )
                    if seed.transform == "identity"
                    else transform_adapter.normalize(
                        payload,
                        request,
                        transform=seed.transform,
                        fetched_at=now,
                        release_from_vintage=initial_release,
                    )
                )
                if not initial_release:
                    observations = [
                        replace(
                            observation,
                            quality_flags=frozenset(
                                set(observation.quality_flags)
                                | {QualityFlag.RETROSPECTIVE_REVISED}
                            ),
                        )
                        for observation in observations
                    ]
                rows_fetched += len(observations)
                for observation in observations:
                    rows_written += int(
                        self.repository.save_observation(
                            observation,
                            sync_run_id=sync_run_id,
                            preserve_vintage=True,
                        ).inserted
                    )
            except (FredClientError, SourcePayloadError) as exc:
                errors.append(f"{seed.code}:{type(exc).__name__}")
                error_details.append(f"{seed.code}:{type(exc).__name__}:{exc}")
        status = (
            "failed"
            if len(errors) == len(selected_seeds)
            else "partial"
            if errors
            else "succeeded"
        )
        self.repository.finish_sync_run(
            sync_run_id,
            finished_at=datetime.now(timezone.utc),
            status=status,
            rows_fetched=rows_fetched,
            rows_written=rows_written,
            error_code="source_errors" if errors else "",
            error_detail=",".join(errors),
        )
        overview = self.recompute(snapshot_at=now) if rows_fetched and recompute_after else None
        return {
            "sync_run_id": sync_run_id,
            "status": status,
            "rows_fetched": rows_fetched,
            "rows_written": rows_written,
            "stage": None if overview is None else overview.stage.value,
            "errors": errors,
            "error_details": error_details,
            "skipped": skipped,
        }

    async def sync_fred_calendar(
        self,
        client: FredClient,
        *,
        fetched_at: datetime | None = None,
        days: int = 45,
    ) -> dict[str, int | str]:
        self.bootstrap()
        now = fetched_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        if days < 1 or days > 90:
            raise ValueError("calendar days must be between 1 and 90")
        end_date = now.date() + timedelta(days=days)
        try:
            payload = await client.fetch_release_dates(start_date=now.date(), end_date=end_date)
            events = FredCalendarAdapter().normalize(
                payload,
                fetched_at=now,
                start_date=now.date(),
                end_date=end_date,
            )
            written = self.repository.save_release_events(
                events, window_start=now.date(), window_end=end_date
            )
        except (FredClientError, SourcePayloadError) as exc:
            return {
                "status": "failed",
                "rows_fetched": 0,
                "rows_written": 0,
                "error": type(exc).__name__,
            }
        return {
            "status": "succeeded",
            "rows_fetched": len(events),
            "rows_written": written,
            "error": "",
        }

    async def sync_bea(
        self,
        client: BeaClient,
        *,
        fetched_at: datetime | None = None,
        recompute_after: bool = True,
    ) -> dict[str, int | str | None]:
        self.bootstrap()
        now = fetched_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        sync_run_id = self.repository.start_sync_run("bea", started_at=now)
        rows_fetched = 0
        rows_written = 0
        error = ""
        try:
            payload = await client.fetch_real_gdp(as_of=now)
            observations = BeaAdapter().normalize_real_gdp(payload, fetched_at=now)
            rows_fetched = len(observations)
            for observation in observations:
                rows_written += int(
                    self.repository.save_observation(observation, sync_run_id=sync_run_id).inserted
                )
        except (OfficialSourceError, SourcePayloadError) as exc:
            error = type(exc).__name__
        status = "failed" if error else "succeeded"
        self.repository.finish_sync_run(
            sync_run_id,
            finished_at=datetime.now(timezone.utc),
            status=status,
            rows_fetched=rows_fetched,
            rows_written=rows_written,
            error_code="source_error" if error else "",
            error_detail=error,
        )
        overview = self.recompute(snapshot_at=now) if rows_fetched and recompute_after else None
        return {
            "sync_run_id": sync_run_id,
            "status": status,
            "rows_fetched": rows_fetched,
            "rows_written": rows_written,
            "stage": None if overview is None else overview.stage.value,
        }

    async def sync_eia(
        self,
        client: EiaClient,
        *,
        fetched_at: datetime | None = None,
        recompute_after: bool = True,
    ) -> dict[str, int | str | None]:
        self.bootstrap()
        now = fetched_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        sync_run_id = self.repository.start_sync_run("eia", started_at=now)
        rows_fetched = 0
        rows_written = 0
        error = ""
        try:
            start_date = (now.date().replace(day=1) - timedelta(days=400)).isoformat()
            payload = await client.fetch_wti(start_date=start_date)
            observations = EiaAdapter().normalize_wti_90d_change(payload, fetched_at=now)
            rows_fetched = len(observations)
            for observation in observations:
                rows_written += int(
                    self.repository.save_observation(observation, sync_run_id=sync_run_id).inserted
                )
        except (OfficialSourceError, SourcePayloadError) as exc:
            error = type(exc).__name__
        status = "failed" if error else "succeeded"
        self.repository.finish_sync_run(
            sync_run_id,
            finished_at=datetime.now(timezone.utc),
            status=status,
            rows_fetched=rows_fetched,
            rows_written=rows_written,
            error_code="source_error" if error else "",
            error_detail=error,
        )
        overview = self.recompute(snapshot_at=now) if rows_fetched and recompute_after else None
        return {
            "sync_run_id": sync_run_id,
            "status": status,
            "rows_fetched": rows_fetched,
            "rows_written": rows_written,
            "stage": None if overview is None else overview.stage.value,
        }

    async def sync_ecb(
        self,
        client: EcbClient,
        *,
        fetched_at: datetime | None = None,
        recompute_after: bool = True,
    ) -> dict[str, int | str | None]:
        self.bootstrap()
        now = fetched_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        sync_run_id = self.repository.start_sync_run("ecb", started_at=now)
        rows_fetched = 0
        rows_written = 0
        error = ""
        try:
            payload = await client.fetch_ciss(as_of=now)
            observations = EcbAdapter().normalize_ciss(payload, fetched_at=now)
            rows_fetched = len(observations)
            for observation in observations:
                rows_written += int(
                    self.repository.save_observation(observation, sync_run_id=sync_run_id).inserted
                )
        except (EuropeSourceError, SourcePayloadError) as exc:
            error = type(exc).__name__
        status = "failed" if error else "succeeded"
        self.repository.finish_sync_run(
            sync_run_id,
            finished_at=datetime.now(timezone.utc),
            status=status,
            rows_fetched=rows_fetched,
            rows_written=rows_written,
            error_code="source_error" if error else "",
            error_detail=error,
        )
        overview = self.recompute(snapshot_at=now) if rows_fetched and recompute_after else None
        return {
            "sync_run_id": sync_run_id,
            "status": status,
            "rows_fetched": rows_fetched,
            "rows_written": rows_written,
            "stage": None if overview is None else overview.stage.value,
        }

    async def sync_eurostat(
        self,
        client: EurostatClient,
        *,
        fetched_at: datetime | None = None,
        recompute_after: bool = True,
    ) -> dict[str, int | str | None]:
        self.bootstrap()
        now = fetched_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        sync_run_id = self.repository.start_sync_run("eurostat", started_at=now)
        rows_fetched = 0
        rows_written = 0
        error = ""
        try:
            payload = await client.fetch_real_gdp(as_of=now)
            observations = EurostatAdapter().normalize_real_gdp(payload, fetched_at=now)
            rows_fetched = len(observations)
            for observation in observations:
                rows_written += int(
                    self.repository.save_observation(observation, sync_run_id=sync_run_id).inserted
                )
        except (EuropeSourceError, SourcePayloadError) as exc:
            error = type(exc).__name__
        status = "failed" if error else "succeeded"
        self.repository.finish_sync_run(
            sync_run_id,
            finished_at=datetime.now(timezone.utc),
            status=status,
            rows_fetched=rows_fetched,
            rows_written=rows_written,
            error_code="source_error" if error else "",
            error_detail=error,
        )
        overview = self.recompute(snapshot_at=now) if rows_fetched and recompute_after else None
        return {
            "sync_run_id": sync_run_id,
            "status": status,
            "rows_fetched": rows_fetched,
            "rows_written": rows_written,
            "stage": None if overview is None else overview.stage.value,
        }

    async def sync_world_bank(
        self,
        client: WorldBankClient,
        *,
        fetched_at: datetime | None = None,
        recompute_after: bool = True,
    ) -> dict[str, int | str | None]:
        self.bootstrap()
        now = fetched_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        sync_run_id = self.repository.start_sync_run("world_bank", started_at=now)
        adapter = WorldBankAdapter()
        rows_fetched = 0
        rows_written = 0
        errors = []
        countries = (
            tuple(sorted(WorldBankClient.SUPPORTED_COUNTRIES))
            if self.feature_flags.global_sources_v2
            else ("CHN", "WLD")
        )
        for country in countries:
            try:
                payload = await client.fetch_gdp_growth(country, as_of=now)
                observations = adapter.normalize_gdp_growth(
                    payload, country=country, fetched_at=now
                )
                rows_fetched += len(observations)
                for observation in observations:
                    rows_written += int(
                        self.repository.save_observation(
                            observation, sync_run_id=sync_run_id
                        ).inserted
                    )
            except (GlobalSourceError, SourcePayloadError) as exc:
                errors.append(f"{country}:{type(exc).__name__}")
        status = "failed" if len(errors) == len(countries) else "partial" if errors else "succeeded"
        self.repository.finish_sync_run(
            sync_run_id,
            finished_at=datetime.now(timezone.utc),
            status=status,
            rows_fetched=rows_fetched,
            rows_written=rows_written,
            error_code="source_errors" if errors else "",
            error_detail=",".join(errors),
        )
        overview = self.recompute(snapshot_at=now) if rows_fetched and recompute_after else None
        return {
            "sync_run_id": sync_run_id,
            "status": status,
            "rows_fetched": rows_fetched,
            "rows_written": rows_written,
            "stage": None if overview is None else overview.stage.value,
        }

    async def sync_bis(
        self,
        client: BisClient,
        *,
        fetched_at: datetime | None = None,
        recompute_after: bool = True,
    ) -> dict[str, int | str | None]:
        self.bootstrap()
        now = fetched_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        sync_run_id = self.repository.start_sync_run("bis", started_at=now)
        rows_fetched = 0
        rows_written = 0
        errors = []
        adapter = BisAdapter()
        fetchers = [
            (
                "credit_gap",
                client.fetch_credit_gaps,
                lambda payload: adapter.normalize_credit_gaps(
                    payload,
                    fetched_at=now,
                    include_global=self.feature_flags.global_sources_v2,
                ),
            )
        ]
        if self.feature_flags.scoring_v11:
            fetchers.extend(
                (
                    (
                        "debt_service",
                        client.fetch_debt_service_ratios,
                        lambda payload: adapter.normalize_debt_service_gaps(
                            payload, fetched_at=now
                        ),
                    ),
                    (
                        "property_prices",
                        client.fetch_residential_property_prices,
                        lambda payload: adapter.normalize_residential_property_prices(
                            payload, fetched_at=now
                        ),
                    ),
                )
            )
        for dataset, fetch, normalize in fetchers:
            try:
                observations = normalize(await fetch())
            except (GlobalSourceError, SourcePayloadError) as exc:
                errors.append(f"{dataset}:{type(exc).__name__}")
                continue
            rows_fetched += len(observations)
            for observation in observations:
                rows_written += int(
                    self.repository.save_observation(
                        observation, sync_run_id=sync_run_id
                    ).inserted
                )
        status = "failed" if errors and not rows_fetched else "partial" if errors else "succeeded"
        self.repository.finish_sync_run(
            sync_run_id,
            finished_at=datetime.now(timezone.utc),
            status=status,
            rows_fetched=rows_fetched,
            rows_written=rows_written,
            error_code="source_errors" if errors else "",
            error_detail=",".join(errors),
        )
        overview = self.recompute(snapshot_at=now) if rows_fetched and recompute_after else None
        return {
            "sync_run_id": sync_run_id,
            "status": status,
            "rows_fetched": rows_fetched,
            "rows_written": rows_written,
            "stage": None if overview is None else overview.stage.value,
        }

    async def sync_oecd(
        self,
        client: OecdClient,
        *,
        fetched_at: datetime | None = None,
        recompute_after: bool = True,
    ) -> dict[str, int | str | None]:
        self.bootstrap()
        now = fetched_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        sync_run_id = self.repository.start_sync_run("oecd", started_at=now)
        rows_fetched = 0
        rows_written = 0
        error = ""
        try:
            payload = await client.fetch_composite_leading_indicators(as_of=now)
            observations = OecdAdapter().normalize_cli_momentum(
                payload,
                fetched_at=now,
                include_global=self.feature_flags.global_sources_v2,
            )
            rows_fetched = len(observations)
            for observation in observations:
                rows_written += int(
                    self.repository.save_observation(
                        observation, sync_run_id=sync_run_id
                    ).inserted
                )
        except (GlobalSourceError, SourcePayloadError) as exc:
            error = type(exc).__name__
        status = "failed" if error else "succeeded"
        self.repository.finish_sync_run(
            sync_run_id,
            finished_at=datetime.now(timezone.utc),
            status=status,
            rows_fetched=rows_fetched,
            rows_written=rows_written,
            error_code="source_error" if error else "",
            error_detail=error,
        )
        overview = self.recompute(snapshot_at=now) if rows_fetched and recompute_after else None
        return {
            "sync_run_id": sync_run_id,
            "status": status,
            "rows_fetched": rows_fetched,
            "rows_written": rows_written,
            "stage": None if overview is None else overview.stage.value,
        }

    async def sync_news(
        self,
        client: NewsClient,
        *,
        fetched_at: datetime | None = None,
        recompute_after: bool = True,
    ) -> dict[str, int | str]:
        self.bootstrap()
        now = fetched_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        source_code = client.source_code
        sync_run_id = self.repository.start_sync_run(source_code, started_at=now)
        rows_fetched = 0
        rows_written = 0
        evidence_written = 0
        events_written = 0
        memory_ingested = 0
        memory_errors = 0
        error = ""
        try:
            payload = await client.fetch()
            items = normalize_official_news(source_code, payload, fetched_at=now)
            rows_fetched = len(items)
            for item in items:
                saved = self.repository.save_news_item(item, sync_run_id=sync_run_id)
                rows_written += int(saved.inserted)
                if self.evidence_pipeline is not None and (saved.inserted or saved.updated):
                    try:
                        await self.evidence_pipeline.ingest_news(saved.news_item_id, item)
                        memory_ingested += 1
                    except Exception:
                        memory_errors += 1
                available_scenario_codes = frozenset(
                    definition.code for definition in self.scenarios
                )
                for evidence in classify_news(
                    item,
                    available_scenario_codes=available_scenario_codes,
                ):
                    evidence_written += int(
                        self.repository.save_news_evidence(
                            saved.news_item_id,
                            evidence,
                            methodology_code=METHODOLOGY_CODE,
                            methodology_version=self.methodology_version,
                            rule_version=NEWS_RULE_VERSION,
                        )
                    )
                if self.feature_flags.news_events_v2:
                    candidate = extract_event_candidate(item)
                    if candidate is not None:
                        self.repository.save_event_candidate(saved.news_item_id, candidate)
                        events_written += 1
        except (NewsSourceError, SourcePayloadError) as exc:
            error = type(exc).__name__
        status = "failed" if error else "succeeded"
        self.repository.finish_sync_run(
            sync_run_id,
            finished_at=datetime.now(timezone.utc),
            status=status,
            rows_fetched=rows_fetched,
            rows_written=rows_written,
            error_code="source_error" if error else "",
            error_detail=error,
        )
        recomputed = False
        if (
            not error
            and recompute_after
            and (self.feature_flags.news_events_v2 or self.feature_flags.scoring_v11)
        ):
            recomputed = self.recompute(snapshot_at=now) is not None
        return {
            "sync_run_id": sync_run_id,
            "status": status,
            "rows_fetched": rows_fetched,
            "rows_written": rows_written,
            "evidence_written": evidence_written,
            "events_written": events_written,
            "fusion_recomputed": recomputed,
            "memory_ingested": memory_ingested,
            "memory_errors": memory_errors,
        }

    async def sync_gdelt_discovery(
        self,
        client: GdeltDiscoveryClient,
        *,
        fetched_at: datetime | None = None,
        timespan: str = "1h",
    ) -> dict[str, int | str]:
        self.bootstrap()
        now = fetched_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        sync_run_id = self.repository.start_sync_run("gdelt_discovery", started_at=now)
        rows_fetched = rows_written = events_written = memory_ingested = memory_errors = 0
        error = ""
        try:
            payload = await client.fetch(timespan=timespan)
            items = GdeltDiscoveryAdapter().normalize(payload, fetched_at=now)
            rows_fetched = len(items)
            for item in items:
                saved = self.repository.save_news_item(item, sync_run_id=sync_run_id)
                rows_written += int(saved.inserted)
                if self.evidence_pipeline is not None and (saved.inserted or saved.updated):
                    try:
                        await self.evidence_pipeline.ingest_news(saved.news_item_id, item)
                        memory_ingested += 1
                    except Exception:
                        memory_errors += 1
                candidate = extract_event_candidate(item)
                if candidate is not None:
                    self.repository.save_event_candidate(saved.news_item_id, candidate)
                    events_written += 1
        except (NewsSourceError, SourcePayloadError) as exc:
            error = type(exc).__name__
        status = "failed" if error else "succeeded"
        self.repository.finish_sync_run(
            sync_run_id,
            finished_at=datetime.now(timezone.utc),
            status=status,
            rows_fetched=rows_fetched,
            rows_written=rows_written,
            error_code="source_error" if error else "",
            error_detail=error,
        )
        return {
            "sync_run_id": sync_run_id,
            "status": status,
            "rows_fetched": rows_fetched,
            "rows_written": rows_written,
            "events_written": events_written,
            "memory_ingested": memory_ingested,
            "memory_errors": memory_errors,
        }

    async def sync_bybit(
        self,
        client: BybitClient,
        *,
        fetched_at: datetime | None = None,
        recompute_after: bool = True,
    ) -> dict[str, int | str | None]:
        self.bootstrap()
        now = fetched_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        sync_run_id = self.repository.start_sync_run("bybit", started_at=now)
        adapter = BybitAdapter()
        rows_fetched = 0
        rows_written = 0
        errors = []
        for symbol in ("BTCUSDT", "ETHUSDT"):
            try:
                funding_payload = await client.fetch_funding(symbol)
                oi_payload = await client.fetch_open_interest(symbol)
                kline_payload = await client.fetch_daily_klines(symbol)
                payloads = (
                    (funding_payload, adapter.normalize_funding),
                    (oi_payload, adapter.normalize_oi_change),
                    (kline_payload, adapter.normalize_drawdown),
                )
                for payload, normalize in payloads:
                    observations = normalize(payload, symbol=symbol, fetched_at=now)
                    rows_fetched += len(observations)
                    for observation in observations:
                        rows_written += int(
                            self.repository.save_observation(
                                observation, sync_run_id=sync_run_id
                            ).inserted
                        )
                if self.feature_flags.scoring_v11:
                    signed = adapter.normalize_signed_oi_changes(
                        oi_payload,
                        symbol=symbol,
                        fetched_at=now,
                    )
                    rows_fetched += len(signed)
                    for observation in signed:
                        rows_written += int(
                            self.repository.save_observation(
                                observation, sync_run_id=sync_run_id
                            ).inserted
                        )
            except (BybitSourceError, SourcePayloadError) as exc:
                errors.append(f"{symbol}:{type(exc).__name__}")
        status = "failed" if len(errors) == 2 else "partial" if errors else "succeeded"
        self.repository.finish_sync_run(
            sync_run_id,
            finished_at=datetime.now(timezone.utc),
            status=status,
            rows_fetched=rows_fetched,
            rows_written=rows_written,
            error_code="source_errors" if errors else "",
            error_detail=",".join(errors),
        )
        overview = self.recompute(snapshot_at=now) if rows_fetched and recompute_after else None
        return {
            "sync_run_id": sync_run_id,
            "status": status,
            "rows_fetched": rows_fetched,
            "rows_written": rows_written,
            "stage": None if overview is None else overview.stage.value,
        }

    async def backfill_bybit(
        self,
        client: BybitClient,
        *,
        started_on: date,
        ended_on: date,
        fetched_at: datetime | None = None,
        recompute_after: bool = True,
        indicator_codes: set[str] | None = None,
    ) -> dict[str, int | str | None | list[str]]:
        """Backfill bounded public Bybit history without creating retrospective labels."""
        self.bootstrap()
        if ended_on < started_on:
            raise ValueError("Bybit backfill end date must not precede start date")
        if (ended_on - started_on).days > 365 * 10 + 3:
            raise ValueError("Bybit backfill window must not exceed 10 years")
        now = fetched_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        if ended_on > now.date():
            raise ValueError("Bybit backfill cannot request future dates")
        known_codes = {
            item.code
            for item in BYBIT_INDICATORS + BYBIT_RESEARCH_INDICATORS + BYBIT_SIGNED_V11_INDICATORS
        }
        if indicator_codes is not None and (not indicator_codes or not indicator_codes <= known_codes):
            raise ValueError("indicator_codes must contain known Bybit indicator codes")

        started_at = datetime(
            started_on.year, started_on.month, started_on.day, tzinfo=timezone.utc
        )
        requested_end = datetime(
            ended_on.year,
            ended_on.month,
            ended_on.day,
            23,
            59,
            59,
            999000,
            tzinfo=timezone.utc,
        )
        ended_at = min(requested_end, now)
        sync_run_id = self.repository.start_sync_run("bybit", started_at=now)
        adapter = BybitAdapter()
        rows_fetched = 0
        rows_written = 0
        errors: list[str] = []
        attempted = 0

        for symbol in ("BTCUSDT", "ETHUSDT"):
            prefix = symbol.removesuffix("USDT").lower()
            jobs = (
                (
                    (f"{prefix}_funding_rate",),
                    f"{prefix}_funding_rate",
                    lambda: client.fetch_funding_history(
                        symbol, started_at=started_at, ended_at=ended_at
                    ),
                    lambda payload: adapter.normalize_daily_funding_history(
                        payload,
                        symbol=symbol,
                        fetched_at=now,
                        started_at=started_at,
                        ended_at=ended_at,
                    ),
                ),
                (
                    (
                        f"{prefix}_oi_7d_abs_change",
                        f"{prefix}_open_interest",
                        f"{prefix}_oi_7d_change",
                    ),
                    f"{prefix}_open_interest_history",
                    lambda: client.fetch_open_interest_history(
                        symbol,
                        started_at=started_at - timedelta(days=8),
                        ended_at=ended_at,
                    ),
                    lambda payload: (
                        adapter.normalize_oi_change_history(
                            payload,
                            symbol=symbol,
                            fetched_at=now,
                            started_at=started_at,
                            ended_at=ended_at,
                        )
                        + adapter.normalize_oi_research_history(
                            payload,
                            symbol=symbol,
                            fetched_at=now,
                            started_at=started_at,
                            ended_at=ended_at,
                        )
                        + (
                            [
                                item
                                for item in adapter.normalize_signed_oi_changes(
                                    payload,
                                    symbol=symbol,
                                    fetched_at=now,
                                )
                                if started_at <= item.observed_at <= ended_at
                            ]
                            if self.feature_flags.scoring_v11
                            else []
                        )
                    ),
                ),
                (
                    (
                        f"{prefix}_30d_drawdown",
                        f"{prefix}_close_price",
                        f"{prefix}_return_7d",
                    ),
                    f"{prefix}_kline_history",
                    lambda: client.fetch_kline_history(
                        symbol,
                        started_at=started_at - timedelta(days=31),
                        ended_at=ended_at,
                    ),
                    lambda payload: (
                        adapter.normalize_drawdown_history(
                            payload,
                            symbol=symbol,
                            fetched_at=now,
                            started_at=started_at,
                            ended_at=ended_at,
                        )
                        + adapter.normalize_price_research_history(
                            payload,
                            symbol=symbol,
                            fetched_at=now,
                            started_at=started_at,
                            ended_at=ended_at,
                        )
                    ),
                ),
            )
            for job_codes, error_code, fetch, normalize in jobs:
                if indicator_codes is not None and not set(job_codes) & indicator_codes:
                    continue
                attempted += 1
                try:
                    observations = [
                        item
                        for item in normalize(await fetch())
                        if indicator_codes is None or item.indicator_code in indicator_codes
                    ]
                    rows_fetched += len(observations)
                    for observation in observations:
                        rows_written += int(
                            self.repository.save_observation(
                                observation, sync_run_id=sync_run_id
                            ).inserted
                        )
                except (BybitSourceError, SourcePayloadError) as exc:
                    errors.append(f"{error_code}:{type(exc).__name__}")

        status = "failed" if len(errors) == attempted else "partial" if errors else "succeeded"
        self.repository.finish_sync_run(
            sync_run_id,
            finished_at=datetime.now(timezone.utc),
            status=status,
            rows_fetched=rows_fetched,
            rows_written=rows_written,
            error_code="source_errors" if errors else "",
            error_detail=",".join(errors),
        )
        overview = self.recompute(snapshot_at=now) if rows_fetched and recompute_after else None
        return {
            "sync_run_id": sync_run_id,
            "status": status,
            "rows_fetched": rows_fetched,
            "rows_written": rows_written,
            "stage": None if overview is None else overview.stage.value,
            "errors": errors,
        }

    def recompute(self, *, snapshot_at: datetime | None = None) -> MarketOverview | None:
        now = snapshot_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("snapshot_at must be timezone-aware")
        inputs = self.repository.latest_analysis_inputs(
            METHODOLOGY_CODE, self.methodology_version
        )
        if not inputs:
            return None
        states = []
        for item in inputs:
            base_state = build_indicator_state(
                item.observation,
                group_code=item.group_code,
                thresholds=item.thresholds,
                max_staleness_seconds=item.max_staleness_seconds,
                snapshot_at=now,
            )
            confirmation_points = (
                1
                if item.frequency in {"monthly", "quarterly", "annual"}
                else STABILITY_POLICY.confirmation_points
            )
            states.append(
                stabilize_indicator_state(
                    base_state,
                    previous_band=self.repository.latest_indicator_band(
                        item.observation.indicator_code,
                        methodology_id=item.methodology_id,
                    ),
                    recent_values=self.repository.recent_indicator_values(
                        item.observation.indicator_code,
                        limit=max(confirmation_points + 1, 3),
                    ),
                    thresholds=item.thresholds,
                    confirmation_points=confirmation_points,
                )
            )
        coverage = None
        if self.feature_flags.coverage_gate:
            coverage = assess_coverage(
                states,
                expected=tuple(
                    ExpectedIndicator(
                        code=item.code,
                        group_code=item.group_code,
                        region_code=item.region_code,
                    )
                    for item in self.indicators
                ),
                required_regions=(
                    GLOBAL_V2_REQUIRED_REGIONS
                    if self.feature_flags.global_sources_v2
                    else DEFAULT_REQUIRED_REGIONS
                ),
            )
        overview = build_market_overview(states, snapshot_at=now, coverage=coverage)
        methodology_ids = {item.methodology_id for item in inputs}
        if len(methodology_ids) != 1:
            raise RuntimeError("analysis inputs contain mixed methodologies")
        methodology_id = methodology_ids.pop()
        self.repository.save_analysis_snapshot(states, overview, methodology_id=methodology_id)
        if coverage is not None:
            self.repository.record_data_health_transition(
                methodology_id=methodology_id,
                snapshot_at=now,
                status=coverage.status.value,
                ratio=coverage.ratio,
                missing_groups=coverage.missing_required_groups,
                missing_regions=coverage.missing_required_regions,
                reason_codes=coverage.reason_codes,
            )
        trend_features = []
        contagion = None
        if self.feature_flags.trend_engine_v2 or self.feature_flags.scoring_v11:
            trend_series = {}
            for item in inputs:
                code = item.observation.indicator_code
                points = self.repository.indicator_points_as_of(code, as_of=now)
                if not points:
                    continue
                trend_series[code] = points
                trend_features.append(
                    calculate_indicator_features(
                        code,
                        points,
                        snapshot_at=now,
                        direction=item.thresholds.direction,
                    )
                )
            contagion = calculate_contagion(
                tuple(trend_features),
                trend_series,
                snapshot_at=now,
            )
            self.repository.save_trend_features(
                tuple(trend_features),
                contagion,
                methodology_id=methodology_id,
            )
        if self.feature_flags.scenario_fusion_v2:
            event_payload = self.repository.events_payload(days=30, limit=100, as_of=now)
            fusion_states = fuse_scenarios(
                self.scenarios,
                groups=overview.groups,
                features=tuple(trend_features),
                indicator_groups={
                    item.observation.indicator_code: item.group_code for item in inputs
                },
                contagion=contagion,
                events=tuple(event_payload.get("items") or ()),
                snapshot_at=now,
                coverage_status=None if coverage is None else coverage.status,
                coverage_ratio=None if coverage is None else coverage.ratio,
                available_group_codes=(
                    None if coverage is None else coverage.available_group_codes
                ),
            )
            self.repository.save_scenario_fusion_states(
                fusion_states,
                methodology_id=methodology_id,
            )
            scenario_states = tuple(
                state.as_scenario_state(definition.horizon)
                for state, definition in zip(fusion_states, self.scenarios, strict=True)
            )
        else:
            scenario_states = build_scenario_states(
                overview.groups,
                available_group_codes=(
                    None if coverage is None else coverage.available_group_codes
                ),
                definitions=self.scenarios,
            )
        self.repository.save_scenario_snapshot(
            scenario_states,
            methodology_id=methodology_id,
            snapshot_at=now,
        )
        if self.feature_flags.scoring_v11:
            self._recompute_v11_shadow(
                snapshot_at=now,
                baseline_overview=overview,
                baseline_methodology_id=methodology_id,
                coverage=coverage,
            )
        return overview

    def _recompute_v11_shadow(
        self,
        *,
        snapshot_at: datetime,
        baseline_overview: MarketOverview,
        baseline_methodology_id: int,
        coverage,
    ) -> None:
        bootstrap_v11_catalog(self.repository)
        inputs = self.repository.analysis_inputs_as_of(
            METHODOLOGY_CODE,
            METHODOLOGY_V11_VERSION,
            as_of=snapshot_at,
        )
        if not inputs:
            return
        candidate_methodology_ids = {item.methodology_id for item in inputs}
        if len(candidate_methodology_ids) != 1:
            raise RuntimeError("v11 analysis inputs contain mixed methodologies")
        candidate_methodology_id = candidate_methodology_ids.pop()
        scores = []
        candidate_states = []
        assignments = {}
        for item in inputs:
            base_state = build_indicator_state(
                item.observation,
                group_code=item.group_code,
                thresholds=item.thresholds,
                max_staleness_seconds=item.max_staleness_seconds,
                snapshot_at=snapshot_at,
            )
            candidate_states.append(base_state)
            points = self.repository.indicator_points_as_of(
                item.observation.indicator_code,
                as_of=snapshot_at,
            )
            if not points:
                continue
            features = calculate_indicator_features(
                item.observation.indicator_code,
                points,
                snapshot_at=snapshot_at,
                direction=item.thresholds.direction,
            )
            score = score_indicator_v2(
                indicator_code=item.observation.indicator_code,
                frequency=item.frequency,
                direction=item.thresholds.direction,
                economic_score=base_state.stress_score,
                features=features,
                history_count=len(points),
                freshness=base_state.freshness,
                data_quality={
                    "fresh": Decimal("1"),
                    "delayed": Decimal(".70"),
                    "stale": Decimal("0"),
                    "missing": Decimal("0"),
                }[base_state.freshness.value],
            )
            scores.append(score)
            seed = next(
                seed
                for seed in V11_INDICATORS
                if seed.code == item.observation.indicator_code
            )
            assignments[seed.code] = dependency_for(
                code=seed.code,
                group_code=seed.group_code,
                region_code=seed.region_code,
            )
        candidate_coverage = assess_coverage(
            candidate_states,
            expected=tuple(
                ExpectedIndicator(
                    code=seed.code,
                    group_code=seed.group_code,
                    region_code=seed.region_code,
                )
                for seed in V11_INDICATORS
            ),
            required_groups=V11_REQUIRED_GROUPS,
            required_regions=GLOBAL_V2_REQUIRED_REGIONS,
        )
        previous_stage, peak = self.repository.latest_v2_stage_context(
            methodology_id=candidate_methodology_id
        )
        stage = calculate_stage_v2(
            tuple(scores),
            assignments,
            coverage_status=candidate_coverage.status,
            previous_stage=previous_stage,
            previous_peak_intensity=peak,
        )
        news_coverage = self.repository.save_news_coverage_snapshot(
            methodology_id=candidate_methodology_id,
            snapshot_at=snapshot_at,
        )
        self.repository.save_v2_shadow_snapshot(
            tuple(scores),
            stage,
            snapshot_at=snapshot_at,
            candidate_methodology_id=candidate_methodology_id,
            baseline_methodology_id=baseline_methodology_id,
            baseline_stage=baseline_overview.stage.value,
            coverage_status=candidate_coverage.status,
        )
        events = tuple(self.repository.events_payload(days=90, limit=100, as_of=snapshot_at).get("items") or ())
        previous_scenarios = self.repository.latest_scenario_v2_context(
            methodology_id=candidate_methodology_id
        )
        scenario_states = []
        for definition in V11_SCENARIOS:
            event_ids = tuple(
                int(event["id"])
                for event in events
                if event.get("taxonomy") in definition.event_taxonomies
                and Decimal(str(event.get("event_score") or 0)) > Decimal(".10")
            )[:20]
            previous = previous_scenarios.get(definition.code)
            scenario_states.append(
                calculate_scenario_v2(
                    definition,
                    stage.groups,
                    evidence_ids=event_ids,
                    numeric_coverage=candidate_coverage.ratio,
                    news_coverage=Decimal(str(news_coverage["ratio"])),
                    previous_status=None if previous is None else previous[0],
                    previous_strength=None if previous is None else previous[1],
                )
            )
        self.repository.save_scenario_states_v2(
            tuple(scenario_states),
            methodology_id=candidate_methodology_id,
            snapshot_at=snapshot_at,
            baseline_stage=baseline_overview.stage.value,
        )
        self.repository.resolve_signal_scorecards(
            methodology_version=METHODOLOGY_V11_VERSION,
            as_of=snapshot_at,
        )

    def v2_shadow(self, *, locale: str = "ru") -> dict:
        return self.repository.latest_v2_overview_payload(
            methodology_version=METHODOLOGY_V11_VERSION,
            locale=locale,
        )

    def scenario_v2(self, *, locale: str = "ru") -> dict:
        return self.repository.scenario_v2_payload(
            methodology_version=METHODOLOGY_V11_VERSION,
            locale=locale,
        )

    def exposure_overlay(self, *, user_id: int, locale: str = "ru") -> dict:
        scenarios = self.scenario_v2(locale=locale)
        return build_exposure_overlay(
            self.repository.open_trade_exposure_inputs(user_id=user_id),
            tuple(scenarios.get("items") or ()),
        )

    def signal_scorecards(self, *, limit: int = 100) -> dict:
        return self.repository.signal_scorecards_payload(
            methodology_version=METHODOLOGY_V11_VERSION,
            limit=limit,
        )

    def search_evidence(
        self,
        query: str,
        *,
        limit: int = 20,
        published_after: datetime | None = None,
        source_codes: tuple[str, ...] = (),
    ) -> dict:
        if self.evidence_pipeline is not None and not source_codes:
            return self.evidence_pipeline.search(
                query, limit=limit, published_after=published_after
            )
        return self.repository.search_evidence_basic(
            query,
            limit=limit,
            published_after=published_after,
            source_codes=source_codes,
        )

    def evidence_memory_health(self) -> dict:
        if self.evidence_pipeline is None:
            return {"ready": True, "profile": "basic-local", "embeddings": False}
        return self.evidence_pipeline.health()

    def overview(self, *, locale: str = "ru", owner_user_id: int = 0) -> dict:
        return self.repository.latest_overview_payload(
            methodology_code=METHODOLOGY_CODE,
            methodology_version=self.methodology_version,
            locale=locale,
            owner_user_id=owner_user_id,
        )

    def save_personal_thresholds(
        self,
        indicator_code: str,
        *,
        owner_user_id: int,
        thresholds,
    ) -> dict:
        threshold_id = self.repository.upsert_personal_thresholds(
            indicator_code,
            methodology_code=METHODOLOGY_CODE,
            methodology_version=self.methodology_version,
            owner_user_id=owner_user_id,
            thresholds=thresholds,
        )
        return {
            "id": threshold_id,
            "indicator_code": indicator_code,
            "methodology": {
                "code": METHODOLOGY_CODE,
                "version": self.methodology_version,
            },
            "scope": "personal",
        }

    def world(
        self, *, locale: str = "ru", as_of: datetime | None = None
    ) -> dict:
        payload = self.repository.regional_contour_payload(
            methodology_code=METHODOLOGY_CODE,
            methodology_version=self.methodology_version,
            locale=locale,
            as_of=as_of,
        )
        localized_names = {
            item.code: item.name_ru if locale == "ru" else item.name
            for item in self.indicators
        }
        for region in payload["regions"]:
            for indicator in region["indicators"]:
                indicator["name"] = localized_names.get(indicator["code"], indicator["name"])
        payload["locale"] = locale
        payload["explanation"] = (
            "Контур показывает только фактически загруженные данные; пропуски отмечены как missing."
            if locale == "ru"
            else "The contour shows only persisted data; unavailable observations are marked missing."
        )
        return payload

    def source_health(
        self, *, locale: str = "ru", as_of: datetime | None = None
    ) -> dict:
        if locale not in {"ru", "en"}:
            raise ValueError("locale must be ru or en")
        payload = self.repository.source_health_payload(as_of=as_of)
        labels = {
            "healthy": {"ru": "работает", "en": "healthy"},
            "degraded": {"ru": "работает с ошибками", "en": "degraded"},
            "failed": {"ru": "ошибка", "en": "failed"},
            "stale": {"ru": "данные устарели", "en": "stale"},
            "running": {"ru": "синхронизация", "en": "running"},
            "never_synced": {"ru": "ещё не синхронизирован", "en": "never synced"},
            "disabled": {"ru": "отключён", "en": "disabled"},
        }
        for source in payload["sources"]:
            source["status_label"] = labels[source["status"]][locale]
        payload["locale"] = locale
        return payload

    def opportunities(
        self,
        *,
        quotes: tuple[MarketQuote, ...] = (),
        locale: str = "ru",
        as_of: datetime | None = None,
        max_ideas: int = 10,
    ) -> dict:
        if locale not in {"ru", "en"}:
            raise ValueError("locale must be ru or en")
        overview = self.overview(locale=locale)
        snapshot_at = overview.get("as_of")
        reference_time = as_of
        if reference_time is None and snapshot_at:
            try:
                reference_time = datetime.fromisoformat(str(snapshot_at))
            except ValueError:
                reference_time = None
        reference_time = reference_time or datetime.now(timezone.utc)
        if reference_time.tzinfo is None or reference_time.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        reference_time = reference_time.astimezone(timezone.utc)

        quality_values: list[Decimal] = []
        for indicator in overview.get("indicators", []):
            raw = indicator.get("quality_score")
            if raw is None:
                continue
            try:
                value = Decimal(str(raw))
            except InvalidOperation:
                continue
            if value.is_finite() and Decimal("0") <= value <= Decimal("1"):
                quality_values.append(value)
        overall_quality = (
            sum(quality_values, Decimal("0")) / Decimal(len(quality_values))
            if quality_values
            else Decimal("0")
        )
        scenarios = tuple(
            ScenarioSignal(
                code=str(item["code"]),
                status=str(item["status"]),
                confidence=str(item["confidence"]),
                horizon=str(item.get("horizon") or "unspecified"),
                evidence_codes=tuple(
                    str(evidence.get("group_code") or evidence.get("code") or "")
                    for evidence in item.get("evidence", [])
                    if isinstance(evidence, dict)
                    and (evidence.get("group_code") or evidence.get("code"))
                ),
            )
            for item in overview.get("scenarios", [])
        )
        try:
            stage = OpportunityMarketStage(str(overview.get("stage", "stable")))
        except ValueError:
            stage = OpportunityMarketStage.STABLE
        ideas = generate_opportunities(
            OpportunityContext(
                as_of=reference_time,
                stage=stage,
                data_quality_score=overall_quality,
                scenarios=scenarios,
                quotes=quotes,
                require_historical_distribution=self.feature_flags.scenario_fusion_v2,
            ),
            max_ideas=max_ideas,
        )
        quotes_by_symbol = {item.symbol: item for item in quotes}

        def localized(text) -> str:
            return text.ru if locale == "ru" else text.en

        return {
            "ready": bool(overview.get("ready")),
            "as_of": reference_time.isoformat(),
            "locale": locale,
            "methodology": overview.get("methodology"),
            "stage": stage.value,
            "data_quality_score": format(overall_quality.quantize(Decimal("0.0001")), "f"),
            "quote_count": len(quotes),
            "available_asset_classes": sorted({item.asset_class.value for item in quotes}),
            "ideas": [
                {
                    "rank": item.rank,
                    "idea_key": item.idea_key,
                    "symbol": item.symbol,
                    "asset_class": item.asset_class.value,
                    "side": item.side.value,
                    "strategy": item.strategy,
                    "score": format(item.score, "f"),
                    "reference_price": (
                        None
                        if item.symbol not in quotes_by_symbol
                        else format(quotes_by_symbol[item.symbol].price, "f")
                    ),
                    "quote_as_of": (
                        None
                        if item.symbol not in quotes_by_symbol
                        else quotes_by_symbol[item.symbol].as_of.isoformat()
                    ),
                    "trigger": localized(item.trigger),
                    "invalidation": localized(item.invalidation),
                    "horizon": item.horizon,
                    "expected_range_pct": {
                        "minimum": format(item.expected_range_pct.minimum, "f"),
                        "maximum": format(item.expected_range_pct.maximum, "f"),
                    },
                    "loss_range_pct": {
                        "minimum": format(item.loss_range_pct.minimum, "f"),
                        "maximum": format(item.loss_range_pct.maximum, "f"),
                    },
                    "historical_distribution": {
                        "sample_size": item.historical_sample_size,
                        "median_pct": (
                            None
                            if item.historical_median_pct is None
                            else format(item.historical_median_pct, "f")
                        ),
                    },
                    "rationale": localized(item.rationale),
                    "evidence": [localized(value) for value in item.evidence],
                    "limitations": [localized(value) for value in item.limitations],
                    "analysis_only": item.analysis_only,
                    "execution_allowed": item.execution_allowed,
                    "personalized_advice": item.personalized_advice,
                }
                for item in ideas
            ],
            "limitations": [
                (
                    "Сейчас автоматически подключены только проверяемые крипто-котировки; "
                    "идеи по TradFi не создаются без настроенного источника цен."
                    if locale == "ru"
                    else "Only verifiable crypto quotes are connected automatically; "
                    "TradFi ideas are not created without a configured price source."
                ),
                (
                    "Диапазоны — условные сценарии, а не обещание доходности. Сделки не создаются."
                    if locale == "ru"
                    else "Ranges are conditional scenarios, not promised returns. No trade is created."
                ),
            ],
        }

    def calendar(self, *, locale: str = "ru", days: int = 30, as_of: date | None = None) -> dict:
        start_date = as_of or datetime.now(timezone.utc).date()
        return self.repository.upcoming_release_payload(
            locale=locale, start_date=start_date, days=days
        )

    def indicator_history(self, code: str, *, limit: int = 180) -> dict | None:
        payload = self.repository.indicator_history_payload(code, limit=limit)
        if payload is None:
            return None
        points = payload.get("points") or []
        if not points:
            payload["event_windows"] = []
            return payload
        coverage_start = str(points[0].get("observed_at") or "")
        coverage_end = str(points[-1].get("observed_at") or "")
        group_code = payload.get("group_code")
        windows: list[dict] = []
        for scenario in self.scenarios:
            if group_code not in scenario.group_codes:
                continue
            catalog = self.repository.event_catalog_payload(scenario.code)
            if catalog is None:
                continue
            for label in catalog.get("labels", []):
                started_at = str(label.get("started_at") or "")
                ended_at = str(label.get("ended_at") or started_at)
                if not started_at or ended_at < coverage_start or started_at > coverage_end:
                    continue
                windows.append(
                    {
                        "scenario_code": scenario.code,
                        "started_at": started_at,
                        "ended_at": ended_at,
                        "label_status": label.get("label_status"),
                        "source_url": label.get("source_url"),
                    }
                )
        payload["event_windows"] = sorted(
            windows,
            key=lambda item: (item["started_at"], item["scenario_code"]),
        )
        return payload

    def backtest_run(self, run_id: int) -> dict | None:
        if run_id < 1:
            return None
        return self.repository.backtest_run_payload(run_id)

    def replay_run(self, run_id: int) -> dict | None:
        if run_id < 1:
            return None
        return self.repository.replay_run_payload(run_id)

    def event_catalog(self, code: str, *, version: str | None = None) -> dict | None:
        if code not in {item.code for item in self.scenarios}:
            return None
        return self.repository.event_catalog_payload(code, version=version)

    def scenario_calibration(self, code: str) -> dict | None:
        if code not in {item.code for item in self.scenarios}:
            return None
        payload = self.repository.latest_backtest_payload(
            code,
            methodology_code=METHODOLOGY_CODE,
            methodology_version=self.methodology_version,
            require_provenance=True,
        )
        if payload is None:
            return {
                "ready": False,
                "scenario_code": code,
                "probability": None,
                "confidence": "insufficient",
                "reason": "no_completed_backtest",
                "historical_backtest": None,
            }
        latest = payload["predictions"][-1] if payload["predictions"] else None
        historical_probability = (
            None if latest is None else latest["calibrated_probability_text"]
        )
        historical_confidence = "insufficient" if latest is None else latest["confidence"]
        metrics = payload["metrics"]
        gate = evaluate_stored_calibration_gate(
            metrics,
            payload.get("parameters", {}).get("promotion_validation"),
        )
        acceptable = historical_probability is not None and gate.passed
        if not acceptable:
            historical_probability = None
            historical_confidence = "insufficient"
        if latest is None or latest["calibrated_probability_text"] is None:
            reason = "insufficient_resolved_history"
        elif not acceptable:
            reason = "calibration_does_not_beat_baseline"
        else:
            reason = "historical_backtest_not_applied_to_live_score"
        return {
            "ready": False,
            "scenario_code": code,
            "probability": None,
            "confidence": "insufficient",
            "reason": reason,
            "historical_backtest": {
                "ready": acceptable,
                "probability": historical_probability,
                "confidence": historical_confidence,
                "run_id": payload["run_id"],
                "completed_at": payload["completed_at"],
                "horizon_seconds": payload["horizon_seconds"],
                "metrics": payload["metrics"],
                "calibration_bins": payload["calibration_bins"],
                "promotion_gate": {
                    "passed": gate.passed,
                    "reasons": gate.reasons,
                    "criteria": gate.criteria,
                },
                "scope": "historical_only",
            },
        }

    def news(
        self,
        *,
        locale: str = "ru",
        days: int = 14,
        limit: int = 20,
        as_of: datetime | None = None,
    ) -> dict:
        return self.repository.news_payload(
            methodology_code=METHODOLOGY_CODE,
            methodology_version=self.methodology_version,
            locale=locale,
            days=days,
            limit=limit,
            as_of=as_of,
        )

    def events(
        self,
        *,
        days: int = 14,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> dict:
        return self.repository.events_payload(days=days, limit=limit, as_of=as_of)

    def trends(self) -> dict:
        inputs = self.repository.latest_analysis_inputs(
            METHODOLOGY_CODE, self.methodology_version
        )
        if not inputs:
            return {"ready": False, "indicators": []}
        methodology_ids = {item.methodology_id for item in inputs}
        if len(methodology_ids) != 1:
            raise RuntimeError("analysis inputs contain mixed methodologies")
        return self.repository.latest_trend_payload(methodology_id=methodology_ids.pop())

    def scenario_fusion(self, *, locale: str = "ru") -> dict:
        inputs = self.repository.latest_analysis_inputs(
            METHODOLOGY_CODE, self.methodology_version
        )
        if not inputs:
            return {"ready": False, "items": []}
        methodology_ids = {item.methodology_id for item in inputs}
        if len(methodology_ids) != 1:
            raise RuntimeError("analysis inputs contain mixed methodologies")
        return self.repository.latest_scenario_fusion_payload(
            methodology_id=methodology_ids.pop(), locale=locale
        )

    def operational_metrics(self) -> dict:
        return self.repository.operational_metrics_payload()
