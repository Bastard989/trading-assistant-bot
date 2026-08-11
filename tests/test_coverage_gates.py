from scripts.check_coverage_gates import GROUPS, evaluate_coverage


def report(*, percentage: float = 100.0, omit: str = "") -> dict:
    files = {}
    for group in GROUPS.values():
        for suffix in group["suffixes"]:
            if suffix == omit:
                continue
            files[suffix] = {
                "summary": {
                    "num_statements": 100,
                    "covered_lines": round(percentage),
                }
            }
    return {"files": files}


def test_coverage_gate_passes_complete_report() -> None:
    result = evaluate_coverage(report())
    assert result["ok"] is True
    assert len(result["groups"]) == 3


def test_coverage_gate_fails_low_or_missing_module() -> None:
    low = evaluate_coverage(report(percentage=50))
    assert low["ok"] is False
    assert all(item["coverage"] == 50 for item in low["groups"])

    suffix = GROUPS["postgres_evidence_memory"]["suffixes"][0]
    missing = evaluate_coverage(report(omit=suffix))
    assert missing["ok"] is False
    assert missing["groups"][-1]["coverage"] is None
