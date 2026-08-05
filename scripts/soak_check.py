from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


MAX_RESPONSE_BYTES = 32_768
MAX_SOAK_DURATION_SECONDS = 14 * 24 * 60 * 60
EXPECTED_STATUS = {
    "/health/live": "ok",
    "/health/ready": "ready",
}


@dataclass(frozen=True)
class ProbeResult:
    endpoint: str
    ok: bool
    status_code: int | None
    latency_ms: int
    detail: str


def validate_base_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must use http or https and include a host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain credentials, query, or fragment")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("plain HTTP is allowed only for loopback health checks")
    return value.rstrip("/") + "/"


def probe(
    base_url: str,
    endpoint: str,
    *,
    timeout_seconds: float,
    opener: Callable = urlopen,
    clock: Callable[[], float] = time.monotonic,
) -> ProbeResult:
    started = clock()
    status_code: int | None = None
    try:
        request = Request(urljoin(base_url, endpoint.lstrip("/")), headers={"Accept": "application/json"})
        with opener(request, timeout=timeout_seconds) as response:
            status_code = int(response.status)
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("health response exceeds size limit")
        payload = json.loads(body.decode("utf-8"))
        expected = EXPECTED_STATUS[endpoint]
        ok = status_code == 200 and payload.get("status") == expected
        detail = "ok" if ok else "unexpected health payload"
    except (HTTPError, URLError, TimeoutError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        ok = False
        detail = type(exc).__name__
    latency_ms = max(0, round((clock() - started) * 1000))
    return ProbeResult(endpoint, ok, status_code, latency_ms, detail)


def run_soak(
    *,
    base_url: str,
    duration_seconds: int,
    interval_seconds: float,
    timeout_seconds: float,
    max_failures: int,
    output: Callable[[str], None] = print,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    probe_fn: Callable[..., ProbeResult] = probe,
) -> dict:
    started = clock()
    deadline = started + duration_seconds
    samples = 0
    failures = 0
    consecutive_failures = 0
    peak_consecutive_failures = 0
    latencies: list[int] = []
    while True:
        results = [
            probe_fn(base_url, endpoint, timeout_seconds=timeout_seconds)
            for endpoint in EXPECTED_STATUS
        ]
        samples += 1
        sample_ok = all(item.ok for item in results)
        if sample_ok:
            consecutive_failures = 0
        else:
            failures += 1
            consecutive_failures += 1
            peak_consecutive_failures = max(peak_consecutive_failures, consecutive_failures)
        latencies.extend(item.latency_ms for item in results)
        output(
            json.dumps(
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "sample": samples,
                    "ok": sample_ok,
                    "consecutive_failures": consecutive_failures,
                    "probes": [asdict(item) for item in results],
                },
                sort_keys=True,
            )
        )
        now = clock()
        if consecutive_failures > max_failures or now >= deadline:
            break
        sleeper(min(interval_seconds, max(0.0, deadline - now)))
    summary = {
        "ok": failures == 0,
        "duration_seconds": max(0, round(clock() - started)),
        "samples": samples,
        "failed_samples": failures,
        "peak_consecutive_failures": peak_consecutive_failures,
        "maximum_allowed_failures": max_failures,
        "average_probe_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "aborted": consecutive_failures > max_failures,
    }
    output(json.dumps({"summary": summary}, sort_keys=True))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded Trading Assistant live/ready soak monitor")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--duration-seconds", type=int, default=300)
    parser.add_argument("--interval-seconds", type=float, default=30)
    parser.add_argument("--timeout-seconds", type=float, default=5)
    parser.add_argument("--max-consecutive-failures", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.duration_seconds <= MAX_SOAK_DURATION_SECONDS:
        parser.error(
            f"duration-seconds must be between 1 and {MAX_SOAK_DURATION_SECONDS}"
        )
    if not 0.1 <= args.interval_seconds <= 3600:
        parser.error("interval-seconds must be between 0.1 and 3600")
    if not 0.1 <= args.timeout_seconds <= 30:
        parser.error("timeout-seconds must be between 0.1 and 30")
    if not 0 <= args.max_consecutive_failures <= 100:
        parser.error("max-consecutive-failures must be between 0 and 100")
    try:
        base_url = validate_base_url(args.base_url)
    except ValueError as exc:
        parser.error(str(exc))
    summary = run_soak(
        base_url=base_url,
        duration_seconds=args.duration_seconds,
        interval_seconds=args.interval_seconds,
        timeout_seconds=args.timeout_seconds,
        max_failures=args.max_consecutive_failures,
    )
    raise SystemExit(0 if summary["ok"] else 1)


if __name__ == "__main__":
    main()
