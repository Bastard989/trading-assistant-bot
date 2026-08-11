import json
from pathlib import Path

from trading_bot.crisis_radar.catalog import METHODOLOGY_V11_VERSION
from trading_bot.crisis_radar.methodology_contract import (
    runtime_methodology_contract,
)
from trading_bot.crisis_radar.replay_v2 import REPLAY_V2_ENGINE_VERSION
from trading_bot.crisis_radar.scoring_v2 import PROFILES, SCORING_VERSION
from trading_bot.crisis_radar.stage_v2 import (
    DEPENDENCY_GRAPH_VERSION,
    STAGE_VERSION,
)


ROOT = Path(__file__).resolve().parents[1]
GUIDE = (ROOT / "docs" / "crisis-radar-guide.md").read_text(encoding="utf-8")
MODEL_CARD = (ROOT / "docs" / "crisis-radar-model-card.md").read_text(encoding="utf-8")
RUNTIME_CONTRACT_PATH = ROOT / "docs" / "crisis-radar-v2-runtime-contract.json"


def test_documented_runtime_versions_match_code() -> None:
    for version in (
        METHODOLOGY_V11_VERSION,
        SCORING_VERSION,
        STAGE_VERSION,
        DEPENDENCY_GRAPH_VERSION,
        REPLAY_V2_ENGINE_VERSION,
    ):
        assert version in GUIDE or version in MODEL_CARD


def test_documented_profiles_match_runtime_weights_and_history_gates() -> None:
    for profile in PROFILES.values():
        assert profile.code in GUIDE
        assert f"| {profile.code} |" in GUIDE
        assert f"| {profile.minimum_history} |" in GUIDE
        for weight in (
            profile.economic,
            profile.historical,
            profile.trend,
            profile.acceleration,
            profile.persistence,
            profile.regime,
        ):
            assert format(weight, "f").lstrip("0") in GUIDE


def test_machine_readable_methodology_contract_matches_runtime_exactly() -> None:
    documented = json.loads(RUNTIME_CONTRACT_PATH.read_text(encoding="utf-8"))

    assert documented == runtime_methodology_contract()
    assert "crisis-radar-v2-runtime-contract.json" in GUIDE


def test_documentation_does_not_claim_v11_probability_or_primary_promotion() -> None:
    combined = f"{GUIDE}\n{MODEL_CARD}"
    assert "live_probability: null" in combined
    assert "candidate-v11: shadow" in combined
    assert "eligible historical financial-stress samples: 0" in combined
    assert "нулевое число v11-точек" in combined
