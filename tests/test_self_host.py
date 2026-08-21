import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from scripts.release_manifest import build_release_manifest
from scripts.release_manager import activate_release, release_status, rollback_release
from scripts.self_host import doctor, migrate_dry_run, service_action, source_contract_check
from trading_bot.crisis_radar.repositories import CrisisRadarRepository
from trading_bot.crisis_radar.service import CrisisRadarService
from trading_bot.db import CURRENT_SCHEMA_VERSION, Database


def test_production_entrypoints_support_direct_execution() -> None:
    repository = Path(__file__).resolve().parents[1]
    entrypoints = (
        "scripts/backup_postgres_daily.py",
        "scripts/release_manifest.py",
        "scripts/radar_canary.py",
        "scripts/radar_snapshot_retention.py",
        "scripts/merge_crisis_replay_history.py",
        "scripts/replay_crisis_radar_v11.py",
        "scripts/self_host.py",
        "scripts/verify_fred_v11_contracts.py",
        "scripts/verify_news_v2_contracts.py",
        "scripts/verify_stablecoin_v16_contract.py",
    )

    for entrypoint in entrypoints:
        completed = subprocess.run(
            [sys.executable, entrypoint, "--help"],
            cwd=repository,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, f"{entrypoint}: {completed.stderr}"
        assert "usage:" in completed.stdout


def test_self_host_doctor_redacts_values_and_dry_run_preserves_working_database(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "assistant.sqlite3"
    database = Database(path)
    CrisisRadarService(CrisisRadarRepository(database)).bootstrap()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")

    report = doctor(path, profile="basic-local")
    before = path.read_bytes()
    migration = migrate_dry_run(path)

    assert report["ok"] is True
    assert "secret-token" not in str(report)
    assert "123" not in str(report)
    assert migration["ok"] is True
    assert migration["schema"] == CURRENT_SCHEMA_VERSION
    assert migration["working_database_changed"] is False
    assert path.read_bytes() == before


def test_server_doctor_rejects_quick_tunnel_and_missing_release(tmp_path, monkeypatch) -> None:
    path = tmp_path / "assistant.sqlite3"
    Database(path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "configured")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "1")
    monkeypatch.setenv("WEB_APP_URL", "https://random.trycloudflare.com")
    monkeypatch.delenv("TRADING_ASSISTANT_RELEASE", raising=False)
    backup_directory = tmp_path / "backups"
    off_host_directory = tmp_path / "off-host"
    backup_directory.mkdir()
    off_host_directory.mkdir()
    monkeypatch.setenv("BACKUP_DIRECTORY", str(backup_directory))
    monkeypatch.setenv("BACKUP_AGE_RECIPIENT", "age1publicrecipient")
    monkeypatch.setenv("OFF_HOST_BACKUP_DIRECTORY", str(off_host_directory))
    monkeypatch.setattr("scripts.self_host.shutil.which", lambda _binary: "/usr/local/bin/age")

    report = doctor(path, profile="server")

    assert report["ok"] is False
    assert set(report["required_failed"]) == {"permanent_https", "release_id"}


def test_source_contract_check_and_service_order(monkeypatch) -> None:
    report = source_contract_check()
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("scripts.self_host.subprocess.run", run)

    assert report["ok"] is True
    assert report["source_count"] == 29
    assert service_action("start")["ok"] is True
    assert calls == [
        ["systemctl", "start", "trading-assistant-api"],
        ["systemctl", "start", "trading-assistant-bot"],
    ]


def test_release_update_and_rollback_are_atomic_and_preserve_releases(tmp_path) -> None:
    releases = tmp_path / "releases"
    old = releases / "old-release"
    new = releases / "new-release"
    old.mkdir(parents=True)
    new.mkdir()
    current = tmp_path / "current"
    state = tmp_path / "state" / "release.json"
    current.symlink_to(old)

    activated = activate_release(releases, "new-release", current, state)
    status = release_status(releases, current, state)
    rolled_back = rollback_release(releases, current, state)

    assert activated == {
        "ok": True,
        "changed": True,
        "current": "new-release",
        "previous": "old-release",
        "manifest": None,
    }
    assert status["current"] == "new-release"
    assert status["previous"] == "old-release"
    assert rolled_back["current"] == "old-release"
    assert current.resolve() == old.resolve()
    assert old.is_dir() and new.is_dir()


def test_release_manifest_refuses_schema_mismatch(tmp_path) -> None:
    releases = tmp_path / "releases"
    release = releases / "release-1"
    release.mkdir(parents=True)
    (release / "release-manifest.json").write_text(
        '{"version":1,"release":"release-1","schema_version":22,'
        '"source_commit":"abcdef123456","created_at":"2026-08-11T00:00:00Z"}',
        encoding="utf-8",
    )

    try:
        activate_release(
            releases,
            "release-1",
            tmp_path / "current",
            tmp_path / "state.json",
            expected_schema=23,
        )
    except ValueError as exc:
        assert "incompatible" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("schema mismatch must fail closed")


def test_release_manifest_enables_schema_checked_activation(tmp_path) -> None:
    releases = tmp_path / "releases"
    release = releases / "release-23"
    release.mkdir(parents=True)
    manifest = build_release_manifest(
        release,
        release="release-23",
        source_commit="abcdef1234567890",
        schema_version=23,
    )

    activated = activate_release(
        releases,
        "release-23",
        tmp_path / "current",
        tmp_path / "state.json",
        expected_schema=23,
    )

    assert manifest["schema_version"] == 23
    assert activated["manifest"]["source_commit"] == "abcdef1234567890"
