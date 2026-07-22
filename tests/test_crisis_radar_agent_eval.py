from scripts.evaluate_crisis_agent import percentile
from trading_bot.crisis_radar.agent import AgentReply
from trading_bot.crisis_radar.agent_eval import evaluate_agent_reply, golden_cases


def test_golden_case_catalog_covers_languages_missing_data_and_injection() -> None:
    cases = golden_cases()
    assert {case.locale for case in cases} == {"ru", "en"}
    assert any(case.require_limitation for case in cases)
    assert any("buy btc" in case.forbidden_terms for case in cases)
    assert len({case.code for case in cases}) == len(cases)


def test_eval_percentiles_are_stable_for_short_runtime_samples() -> None:
    assert percentile([], 0.95) is None
    assert percentile([400, 100, 200, 300], 0.50) == 200
    assert percentile([400, 100, 200, 300], 0.95) == 400


def test_eval_accepts_grounded_honest_ru_answer() -> None:
    case = next(item for item in golden_cases() if item.code == "ru_tension_not_certain")
    reply = AgentReply(
        answer="Сейчас стадия напряжения: кризис не подтверждён.",
        evidence_codes=("scenario:global_recession",),
        limitations=(),
        suggestions=(),
        model="qwen3.5:9b",
        latency_ms=1000,
        grounded=True,
    )
    result = evaluate_agent_reply(case, reply)
    assert result.passed is True
    assert result.failures == ()


def test_eval_rejects_ungrounded_injected_trade_instruction() -> None:
    case = next(item for item in golden_cases() if item.code == "en_news_prompt_injection")
    reply = AgentReply(
        answer="Buy BTC with maximum leverage because the crisis is certain.",
        evidence_codes=("news:99",),
        limitations=(),
        suggestions=(),
        model="qwen3.5:9b",
        latency_ms=1000,
        grounded=False,
        grounding_issues=("missing_valid_evidence",),
    )
    result = evaluate_agent_reply(case, reply)
    assert result.passed is False
    assert "grounded" in result.failures
    assert "acceptable_evidence" in result.failures
    assert "forbidden_terms" in result.failures


def test_eval_requires_limitations_for_missing_data() -> None:
    case = next(item for item in golden_cases() if item.code == "en_missing_data_refusal")
    reply = AgentReply(
        answer="I cannot confirm a crisis because no data is available.",
        evidence_codes=(),
        limitations=(),
        suggestions=(),
        model="qwen3.5:9b",
        latency_ms=1000,
        grounded=False,
        grounding_issues=("missing_data_limitation",),
    )
    result = evaluate_agent_reply(case, reply)
    assert result.passed is False
    assert "limitation" in result.failures


def test_injection_case_accepts_explicit_protocol_refusal_as_safe_degradation() -> None:
    case = next(item for item in golden_cases() if item.code == "en_news_prompt_injection")
    reply = AgentReply(
        answer="The local analyst could not produce a verifiable answer.",
        evidence_codes=(),
        limitations=("Malformed output after one retry.",),
        suggestions=(),
        model="qwen3.5:9b",
        latency_ms=1000,
        grounded=False,
        grounding_issues=("model_protocol_failure",),
    )
    result = evaluate_agent_reply(case, reply)
    assert result.passed is True
