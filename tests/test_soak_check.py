import io

import pytest

from scripts.soak_check import ProbeResult, run_soak, validate_base_url


def test_soak_base_url_requires_https_or_loopback() -> None:
    assert validate_base_url("http://127.0.0.1:8080") == "http://127.0.0.1:8080/"
    assert validate_base_url("https://radar.example.com") == "https://radar.example.com/"
    with pytest.raises(ValueError, match="loopback"):
        validate_base_url("http://radar.example.com")
    with pytest.raises(ValueError, match="credentials"):
        validate_base_url("https://user:secret@radar.example.com")


def test_soak_summary_passes_only_without_failed_samples() -> None:
    now = [0.0]
    lines: list[str] = []

    def clock() -> float:
        now[0] += 0.5
        return now[0]

    def fake_probe(base_url, endpoint, *, timeout_seconds):
        return ProbeResult(endpoint, True, 200, 4, "ok")

    summary = run_soak(
        base_url="http://127.0.0.1:8080/",
        duration_seconds=1,
        interval_seconds=0.1,
        timeout_seconds=1,
        max_failures=1,
        output=lines.append,
        sleeper=lambda _: None,
        clock=clock,
        probe_fn=fake_probe,
    )
    assert summary["ok"] is True
    assert summary["failed_samples"] == 0
    assert any('"summary"' in line for line in lines)


def test_soak_aborts_after_consecutive_failure_budget() -> None:
    outputs = io.StringIO()

    def failed_probe(base_url, endpoint, *, timeout_seconds):
        return ProbeResult(endpoint, False, 503, 2, "unexpected health payload")

    summary = run_soak(
        base_url="http://127.0.0.1:8080/",
        duration_seconds=60,
        interval_seconds=0.1,
        timeout_seconds=1,
        max_failures=1,
        output=lambda line: outputs.write(line + "\n"),
        sleeper=lambda _: None,
        probe_fn=failed_probe,
    )
    assert summary["ok"] is False
    assert summary["aborted"] is True
    assert summary["failed_samples"] == 2
