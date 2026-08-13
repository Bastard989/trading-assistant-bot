import asyncio
import io
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.crisis_radar.feature_flags import CrisisRadarFeatureFlags
from trading_bot.crisis_radar.sources.base import SourcePayloadError
from trading_bot.crisis_radar.sources.global_clients import (
    BisClient,
    GlobalSourceError,
    OecdClient,
    WorldBankClient,
)
from trading_bot.crisis_radar.sources.global_data import BisAdapter, OecdAdapter, WorldBankAdapter
from trading_bot.db import Database


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
BIS_HEADER = (
    "FREQ:Frequency,BORROWERS_CTY:Borrowers' country,TC_BORROWERS:Borrowing sector,"
    "TC_LENDERS:Lending sector,CG_DTYPE:Credit gap data type,"
    "TIME_PERIOD:Time period or range,OBS_VALUE:Observation Value\n"
)
BIS_ROWS = (
    "Q: Quarterly,US: United States,P: Private non-financial sector,A: All sectors,"
    "C: Credit-to-GDP gaps (actual-trend),2025-Q4,-11.5378\n"
    "Q: Quarterly,CN: China,P: Private non-financial sector,A: All sectors,"
    "C: Credit-to-GDP gaps (actual-trend),2025-Q4,-7.6881\n"
)


def _bis_zip(*, filename: str = "WS_CREDIT_GAP_csv_flat.csv") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(filename, BIS_HEADER + BIS_ROWS)
    return output.getvalue()


def _bulk_zip(filename: str, text: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(filename, text)
    return output.getvalue()


def _bis_dsr_zip() -> bytes:
    header = (
        "FREQ:Frequency,BORROWERS_CTY:Borrowers' country,DSR_BORROWERS:Borrowers,"
        "TIME_PERIOD:Time period or range,OBS_VALUE:Observation Value,"
        "UNIT_MEASURE:Unit of measure,OBS_STATUS:Observation Status,"
        "OBS_CONF:Observation confidentiality\n"
    )
    rows = []
    for offset in range(61):
        year, quarter_index = divmod(offset, 4)
        quarter = quarter_index + 1
        value = "16" if offset == 60 else "10"
        rows.append(
            f"Q: Quarterly,US: United States,P: Private non-financial sector,"
            f"{2000 + year}-Q{quarter},{value},367: Per cent,A: Normal value,F: Free\n"
        )
    return _bulk_zip("WS_DSR_csv_flat.csv", header + "".join(rows))


def _bis_property_zip(*, value_kind: str = "R: Real") -> bytes:
    header = (
        "FREQ:Frequency,REF_AREA:Reference area,VALUE:Value,"
        "UNIT_MEASURE:Unit of measure,TIME_PERIOD:Time period or range,"
        "OBS_VALUE:Observation Value,OBS_STATUS:Observation Status,"
        "OBS_CONF:Observation confidentiality\n"
    )
    rows = (
        f"Q: Quarterly,US: United States,{value_kind},"
        '"771: Year-on-year changes, in per cent",2025-Q4,-5.25,'
        "A: Normal value,F: Free\n"
    )
    return _bulk_zip("WS_SPP_csv_flat.csv", header + rows)


def test_world_bank_adapter_normalizes_annual_growth() -> None:
    observations = WorldBankAdapter().normalize_gdp_growth(
        (FIXTURES / "world_bank_china_gdp.json").read_bytes(),
        country="CHN",
        fetched_at=NOW,
    )

    assert [item.observed_at.year for item in observations] == [2023, 2024, 2025]
    assert observations[-1].indicator_code == "china_real_gdp_yoy"
    assert observations[-1].value == Decimal("4.95994886240992")
    assert observations[-1].released_at == datetime(2026, 7, 13, tzinfo=timezone.utc)


def test_bis_adapter_selects_comparable_private_credit_gap_rows() -> None:
    observations = BisAdapter().normalize_credit_gaps(_bis_zip(), fetched_at=NOW)

    assert [(item.indicator_code, item.value) for item in observations] == [
        ("china_credit_to_gdp_gap", Decimal("-7.6881")),
        ("us_credit_to_gdp_gap", Decimal("-11.5378")),
    ]
    assert all(item.unit == "percentage_points" for item in observations)


def test_bis_adapter_rejects_unexpected_archive_entry() -> None:
    with pytest.raises(SourcePayloadError, match="archive contents"):
        BisAdapter().normalize_credit_gaps(_bis_zip(filename="unexpected.csv"), fetched_at=NOW)


def test_bis_depth_adapters_apply_causal_dsr_baseline_and_real_house_price_filter() -> None:
    debt_service = BisAdapter().normalize_debt_service_gaps(
        _bis_dsr_zip(), fetched_at=NOW
    )
    house_prices = BisAdapter().normalize_residential_property_prices(
        _bis_property_zip(), fetched_at=NOW
    )

    assert [(item.indicator_code, item.value) for item in debt_service] == [
        ("us_debt_service_gap", Decimal("6.0000"))
    ]
    assert debt_service[0].observed_at == datetime(2015, 3, 31, tzinfo=timezone.utc)
    assert debt_service[0].released_at == NOW
    assert [(item.indicator_code, item.value) for item in house_prices] == [
        ("us_real_house_price_yoy", Decimal("-5.25"))
    ]
    assert all("release_time_estimated" in {flag.value for flag in item.quality_flags} for item in debt_service + house_prices)


def test_bis_depth_adapters_reject_changed_archive_contract() -> None:
    with pytest.raises(SourcePayloadError, match="archive contents"):
        BisAdapter().normalize_debt_service_gaps(
            _bulk_zip("wrong.csv", "bad"), fetched_at=NOW
        )
    with pytest.raises(SourcePayloadError, match="no usable"):
        BisAdapter().normalize_residential_property_prices(
            _bis_property_zip(value_kind="N: Nominal"), fetched_at=NOW
        )


def test_oecd_adapter_derives_six_month_momentum_without_assuming_row_order() -> None:
    observations = OecdAdapter().normalize_cli_momentum(
        (FIXTURES / "oecd_cli.csv").read_bytes(), fetched_at=NOW
    )

    assert [(item.indicator_code, item.value) for item in observations] == [
        ("china_cli_6m_change", Decimal("-0.3939")),
        ("g20_cli_6m_change", Decimal("0.0901")),
    ]
    assert all(item.observed_at == datetime(2026, 6, 30, tzinfo=timezone.utc) for item in observations)
    assert all(item.unit == "index_points_6m" for item in observations)


def test_oecd_adapter_rejects_changed_dimensions() -> None:
    payload = (FIXTURES / "oecd_cli.csv").read_text().replace(",AA,IX,_Z,H,", ",SA,IX,_Z,H,")
    with pytest.raises(SourcePayloadError, match="dimensions"):
        OecdAdapter().normalize_cli_momentum(payload.encode(), fetched_at=NOW)


def test_global_client_retries_and_never_requires_a_key() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        assert request.url.params["date"] == "2019:2026"
        return httpx.Response(200, content=b"payload")

    async def scenario() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await WorldBankClient(client=client, sleep=lambda _: asyncio.sleep(0)).fetch_gdp_growth(
                "WLD", as_of=NOW
            )

    assert asyncio.run(scenario()) == b"payload"
    assert calls == 2


def test_global_client_sanitizes_http_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(GlobalSourceError, match="HTTP 404"):
                await BisClient(client=client).fetch_credit_gaps()

    asyncio.run(scenario())


def test_bis_client_uses_allowlisted_depth_bulk_endpoints() -> None:
    paths = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.url.host == "data.bis.org"
        assert request.headers["Accept"] == "application/zip"
        return httpx.Response(200, content=b"zip")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            bis = BisClient(client=client)
            assert await bis.fetch_debt_service_ratios() == b"zip"
            assert await bis.fetch_residential_property_prices() == b"zip"

    asyncio.run(scenario())
    assert paths == [
        "/static/bulk/WS_DSR_csv_flat.zip",
        "/static/bulk/WS_SPP_csv_flat.zip",
    ]


def test_oecd_client_uses_bounded_public_sdmx_query() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert "G20+CHN+CAN+GBR+JPN+KOR+IND+BRA+MEX+USA.M.LI...AA...H" in str(
            request.url
        )
        assert request.url.params["startPeriod"] == "2023-01"
        assert request.url.params["endPeriod"] == "2026-07"
        assert request.headers["Accept"] == "text/csv"
        return httpx.Response(200, content=b"csv")

    async def scenario() -> bytes:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await OecdClient(client=client).fetch_composite_leading_indicators(as_of=NOW)

    assert asyncio.run(scenario()) == b"csv"


def test_oecd_global_v2_adapter_adds_regions_only_when_enabled() -> None:
    base = (FIXTURES / "oecd_cli.csv").read_text()
    header, *rows = base.splitlines()
    china_rows = [row.replace("CHN,", "JPN,", 1) for row in rows if "CHN," in row]
    payload = ("\n".join([header, *rows, *china_rows]) + "\n").encode()

    legacy = OecdAdapter().normalize_cli_momentum(payload, fetched_at=NOW)
    global_v2 = OecdAdapter().normalize_cli_momentum(
        payload,
        fetched_at=NOW,
        include_global=True,
    )

    assert "japan_cli_6m_change" not in {item.indicator_code for item in legacy}
    assert "japan_cli_6m_change" in {item.indicator_code for item in global_v2}


class StubWorldBankClient:
    async def fetch_gdp_growth(self, country: str, *, as_of: datetime) -> bytes:
        fixture = "world_bank_china_gdp.json" if country == "CHN" else "world_bank_world_gdp.json"
        return (FIXTURES / fixture).read_bytes()


class StubBisClient:
    async def fetch_credit_gaps(self) -> bytes:
        return _bis_zip()

    async def fetch_debt_service_ratios(self) -> bytes:
        return _bis_dsr_zip()

    async def fetch_residential_property_prices(self) -> bytes:
        return _bis_property_zip()


class PartialBisClient(StubBisClient):
    async def fetch_debt_service_ratios(self) -> bytes:
        raise GlobalSourceError("fixture failure")


class StubOecdClient:
    async def fetch_composite_leading_indicators(self, *, as_of: datetime) -> bytes:
        return (FIXTURES / "oecd_cli.csv").read_bytes()


def test_global_sync_builds_starter_v8_snapshot(tmp_path) -> None:
    service = CrisisRadarService(CrisisRadarRepository(Database(tmp_path / "global.sqlite3")))

    world_bank = asyncio.run(service.sync_world_bank(StubWorldBankClient(), fetched_at=NOW))
    bis = asyncio.run(service.sync_bis(StubBisClient(), fetched_at=NOW))
    oecd = asyncio.run(service.sync_oecd(StubOecdClient(), fetched_at=NOW))

    assert world_bank["status"] == "succeeded"
    assert bis["status"] == "succeeded"
    assert oecd["status"] == "succeeded"
    overview = service.overview(locale="en")
    assert overview["methodology"]["version"] == "starter-v8"
    assert {item["code"] for item in overview["indicators"]} == {
        "china_real_gdp_yoy",
        "world_real_gdp_yoy",
        "us_credit_to_gdp_gap",
        "china_credit_to_gdp_gap",
        "g20_cli_6m_change",
        "china_cli_6m_change",
    }
    assert overview["stage"] == "tension"
    assert {item["code"]: item["band"] for item in overview["groups"]}[
        "china_leading_cycle"
    ] == "warning"
    assert next(
        item for item in overview["scenarios"] if item["code"] == "china_hard_landing"
    )["status"] == "inactive"


def test_bis_depth_sync_collects_disabled_v14_inputs_without_changing_live_stage(
    tmp_path,
) -> None:
    flags = CrisisRadarFeatureFlags(
        thresholds_v2=True,
        global_sources_v2=True,
        scoring_v11=True,
    )
    database = Database(tmp_path / "bis-depth.sqlite3")
    repository = CrisisRadarRepository(database)
    service = CrisisRadarService(repository, feature_flags=flags)

    result = asyncio.run(service.sync_bis(StubBisClient(), fetched_at=NOW))

    assert result["status"] == "succeeded"
    assert result["rows_fetched"] == 4
    with database.connect() as connection:
        depth = connection.execute(
            """
            SELECT indicator.code, indicator.enabled, count(observation.id)
            FROM cr_indicator_definitions AS indicator
            LEFT JOIN cr_observations AS observation
              ON observation.indicator_id=indicator.id
            WHERE indicator.code IN ('us_debt_service_gap', 'us_real_house_price_yoy')
            GROUP BY indicator.code, indicator.enabled
            ORDER BY indicator.code
            """
        ).fetchall()
        live_v14 = connection.execute(
            """
            SELECT count(*)
            FROM cr_market_snapshots_v2 AS snapshot
            JOIN cr_methodology_versions AS methodology
              ON methodology.id=snapshot.methodology_id
            WHERE methodology.version='candidate-v14'
            """
        ).fetchone()[0]
    assert [tuple(row) for row in depth] == [
        ("us_debt_service_gap", 0, 1),
        ("us_real_house_price_yoy", 0, 1),
    ]
    assert live_v14 == 0
    assert service.overview(locale="en")["methodology"]["version"] == "candidate-v10"


def test_bis_depth_dataset_failure_degrades_without_discarding_other_datasets(
    tmp_path,
) -> None:
    service = CrisisRadarService(
        CrisisRadarRepository(Database(tmp_path / "bis-partial.sqlite3")),
        feature_flags=CrisisRadarFeatureFlags(
            thresholds_v2=True,
            global_sources_v2=True,
            scoring_v11=True,
        ),
    )

    result = asyncio.run(service.sync_bis(PartialBisClient(), fetched_at=NOW))

    assert result["status"] == "partial"
    assert result["rows_fetched"] == 3
    assert result["rows_written"] == 3
