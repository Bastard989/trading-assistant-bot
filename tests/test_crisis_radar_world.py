from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from tests.test_api_security import auth_header, load_test_app
from trading_bot.crisis_radar.catalog import STARTER_INDICATORS
from trading_bot.crisis_radar.domain import Observation
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.db import Database


NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)


def _save(service: CrisisRadarService, code: str, value: str) -> None:
    seed = next(item for item in STARTER_INDICATORS if item.code == code)
    service.repository.save_observation(
        Observation(
            indicator_code=seed.code,
            source_code=next(
                source
                for source, codes in {
                    "fred": {"sahm_rule"},
                    "ecb": {"euro_ciss"},
                    "world_bank": {"china_real_gdp_yoy", "world_real_gdp_yoy"},
                }.items()
                if code in codes
            ),
            value=Decimal(value),
            unit=seed.unit,
            observed_at=NOW - timedelta(days=1),
            released_at=NOW - timedelta(hours=12),
            fetched_at=NOW - timedelta(hours=1),
            vintage="2026-07-19",
            content_hash=f"hash-{code}",
        )
    )


def test_world_contour_keeps_fixed_regions_and_marks_missing_data(tmp_path) -> None:
    service = CrisisRadarService(CrisisRadarRepository(Database(tmp_path / "world-missing.sqlite3")))
    service.bootstrap()

    payload = service.world(locale="ru", as_of=NOW)

    assert payload["ready"] is False
    assert [item["code"] for item in payload["regions"]] == ["US", "EU", "CHINA", "GLOBAL"]
    assert [item["name"] for item in payload["regions"]] == ["США", "Европа", "Китай", "Мир"]
    all_indicators = [
        indicator for region in payload["regions"] for indicator in region["indicators"]
    ]
    assert len(all_indicators) == len(STARTER_INDICATORS)
    assert {item["freshness"] for item in all_indicators} == {"missing"}
    assert all(item["value_text"] is None for item in all_indicators)
    assert all(item["provenance"]["observation_id"] is None for item in all_indicators)


def test_world_contour_normalizes_regions_and_preserves_lineage(tmp_path) -> None:
    service = CrisisRadarService(CrisisRadarRepository(Database(tmp_path / "world.sqlite3")))
    service.bootstrap()
    _save(service, "sahm_rule", "0.6")
    _save(service, "euro_ciss", "0.22")
    _save(service, "china_real_gdp_yoy", "4.9")
    _save(service, "world_real_gdp_yoy", "2.8")
    service.recompute(snapshot_at=NOW)

    payload = service.world(locale="en", as_of=NOW)

    assert payload["ready"] is True
    assert payload["snapshot_at"] == NOW.isoformat()
    assert payload["methodology"] == {"code": "crisis-radar", "version": "starter-v8"}
    by_region = {item["code"]: item for item in payload["regions"]}
    assert {item["code"] for item in by_region["US"]["indicators"] if item["value_text"]} == {
        "sahm_rule"
    }
    assert {item["code"] for item in by_region["EU"]["indicators"] if item["value_text"]} == {
        "euro_ciss"
    }
    assert {
        item["code"] for item in by_region["CHINA"]["indicators"] if item["value_text"]
    } == {"china_real_gdp_yoy"}
    assert {
        item["code"] for item in by_region["GLOBAL"]["indicators"] if item["value_text"]
    } == {"world_real_gdp_yoy"}
    china = next(
        item for item in by_region["CHINA"]["indicators"] if item["code"] == "china_real_gdp_yoy"
    )
    assert china["freshness"] == "fresh"
    assert china["observed_at"] == (NOW - timedelta(days=1)).isoformat()
    assert china["released_at"] == (NOW - timedelta(hours=12)).isoformat()
    assert china["fetched_at"] == (NOW - timedelta(hours=1)).isoformat()
    assert china["provenance"]["source"]["code"] == "world_bank"
    assert china["provenance"]["provider_series_id"] == "CHN:NY.GDP.MKTP.KD.ZG"
    assert china["provenance"]["vintage"] == "2026-07-19"
    assert china["provenance"]["content_hash"] == "hash-china_real_gdp_yoy"
    assert china["analysis_pending"] is False


def test_source_health_reports_runs_coverage_and_redacts_errors(tmp_path) -> None:
    repository = CrisisRadarRepository(Database(tmp_path / "health.sqlite3"))
    service = CrisisRadarService(repository)
    service.bootstrap()
    _save(service, "sahm_rule", "0.2")
    succeeded = repository.start_sync_run("fred", started_at=NOW - timedelta(hours=2))
    repository.finish_sync_run(
        succeeded,
        finished_at=NOW - timedelta(hours=1),
        status="succeeded",
        rows_fetched=7,
        rows_written=1,
    )
    failed = repository.start_sync_run("fred", started_at=NOW - timedelta(minutes=10))
    repository.finish_sync_run(
        failed,
        finished_at=NOW - timedelta(minutes=5),
        status="failed",
        rows_fetched=0,
        rows_written=0,
        error_code="upstream_error",
        error_detail="https://example.test/data?api_key=top-secret token=also-secret",
    )
    stale = repository.start_sync_run("eia", started_at=NOW - timedelta(days=21))
    repository.finish_sync_run(
        stale,
        finished_at=NOW - timedelta(days=20),
        status="succeeded",
        rows_fetched=1,
        rows_written=0,
    )

    payload = service.source_health(locale="ru", as_of=NOW)

    fred = next(item for item in payload["sources"] if item["code"] == "fred")
    assert fred["status"] == "degraded"
    assert fred["status_label"] == "работает с ошибками"
    assert fred["latest_run"]["rows_fetched"] == 0
    assert fred["last_success"]["rows_fetched"] == 7
    assert fred["last_failure"]["id"] == failed
    assert fred["rows"] == {"run_count": 2, "fetched_total": 7, "written_total": 1}
    assert fred["coverage"]["indicator_count"] > 1
    assert fred["coverage"]["observed_count"] == 1
    error = fred["latest_run"]["error"]["detail"]
    assert "top-secret" not in error
    assert "also-secret" not in error
    assert "[redacted]" in error
    never_synced = next(item for item in payload["sources"] if item["code"] == "bea")
    assert never_synced["status"] == "never_synced"
    assert never_synced["sync_freshness"] == "missing"
    eia = next(item for item in payload["sources"] if item["code"] == "eia")
    assert eia["status"] == "stale"
    assert eia["sync_freshness"] == "stale"


def test_world_and_source_health_endpoints_require_owner_auth(monkeypatch, tmp_path) -> None:
    module = load_test_app(monkeypatch, tmp_path)
    module.crisis_radar.bootstrap()
    client = TestClient(module.app)

    assert client.get("/api/crisis-radar/world").status_code == 401
    assert client.get("/api/crisis-radar/sources/health").status_code == 401
    world = client.get("/api/crisis-radar/world?locale=en", headers=auth_header(42))
    health = client.get("/api/crisis-radar/sources/health?locale=ru", headers=auth_header(42))
    assert world.status_code == 200
    assert [item["code"] for item in world.json()["regions"]] == ["US", "EU", "CHINA", "GLOBAL"]
    assert health.status_code == 200
    assert health.json()["summary"]["source_count"] == 11
    assert client.get(
        "/api/crisis-radar/world?locale=de", headers=auth_header(42)
    ).status_code == 422
