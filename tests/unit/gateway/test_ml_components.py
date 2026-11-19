"""Unit tests: detector calibration/combination, LLM detector parsing, pruning fields and fallback."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from gateway.detectors.base import Detection
from gateway.detectors.combined import CombinedDetector
from gateway.detectors.llm import LLMDetector
from gateway.detectors.transformer import calibrate
from gateway.optimizer.fields import candidate_paths, humanize, value_kind
from gateway.optimizer.learned import LearnedPruner
from gateway.optimizer.rules import PruningRules
from gateway.pipeline.context import CallContext

ROOT = Path(__file__).resolve().parents[3]


def test_calibrate_maps_threshold_to_half() -> None:
    assert calibrate(0.3, 0.3) == pytest.approx(0.5)
    assert calibrate(0.0, 0.3) == 0.0
    assert calibrate(1.0, 0.3) == pytest.approx(1.0)
    assert calibrate(0.15, 0.3) == pytest.approx(0.25)
    assert calibrate(0.65, 0.3) == pytest.approx(0.75)


class Fixed:
    """Detector returning fixed scores."""

    def __init__(self, name: str, score: float) -> None:
        self.name = name
        self.value = score

    def score(self, text: str) -> Detection:
        return Detection(score=self.value)

    def score_many(self, texts: Sequence[str]) -> list[Detection]:
        return [Detection(score=self.value, matches=(self.name,)) for _ in texts]


def test_combined_detector() -> None:
    det = CombinedDetector([Fixed("a", 1.0), Fixed("b", 0.0)], weights=[4.0, 2.0], bias=-2.0, threshold=0.5)
    out = det.score_many(["x", "y"])
    assert det.probability([1.0, 0.0]) == pytest.approx(0.8808, rel=1e-3)
    assert out[0].score > 0.5
    assert out[0].matches == ("a", "b")
    with pytest.raises(ValueError, match="one weight"):
        CombinedDetector([Fixed("a", 1.0)], weights=[1.0, 2.0], bias=0.0)


def test_llm_detector_parses_and_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = iter(["Sure: [0.1, 0.95]", "[0.5]", "no json"])
    monkeypatch.setattr("gateway.llm.complete", lambda *a, **k: next(replies))
    det = LLMDetector("m", batch=2)
    scores = [d.score for d in det.score_many(["a", "b", "c"])]
    assert scores == [0.1, 0.95, 0.5]
    assert det.requests == 2
    with pytest.raises(ValueError, match="no JSON"):
        det.score_many(["d"])


def test_candidate_paths_and_helpers() -> None:
    data = {
        "id": 1,
        "kyc": {"status": "ok", "documents": ["a"]},
        "items": [{"x": 1, "y": {"z": 2}}],
        "_omitted": {},
    }
    paths = candidate_paths(data)
    assert set(paths) == {"id", "kyc.status", "kyc.documents", "items[].x", "items[].y"}
    assert humanize("holdings[].unrealized_pnl_pct") == "holdings unrealized pnl pct"
    samples: list[Any] = [True, 1.5, "s", [], {}, None]
    assert [value_kind(v) for v in samples] == [
        "bool",
        "number",
        "string",
        "list",
        "object",
        "null",
    ]


async def test_learned_pruner_falls_back_without_question() -> None:
    class Never:
        def predict_proba(self, x: Any) -> Any:
            raise AssertionError("model must not be called without a question")

    bundle = {"model": Never(), "vocab": {}, "tools": ["get_client"], "threshold": 0.5}
    pruner = LearnedPruner(bundle, PruningRules(tools={"get_client": ["email"]}))
    ctx = CallContext(
        agent_id="a", public_name="wealth__get_client", server="wealth", tool="get_client", arguments={}
    )
    assert await pruner.drop_paths(ctx, {"email": "x"}) == ["email"]
    assert pruner.fallbacks == 1
    with pytest.raises(FileNotFoundError):
        LearnedPruner.load(ROOT / "nope.joblib")


@pytest.mark.skipif(
    not (ROOT / "ml" / "artifacts" / "pruning_model.joblib").exists(), reason="model not trained"
)
async def test_trained_learned_pruner_keeps_question_fields() -> None:
    pruner = LearnedPruner.load(ROOT / "ml" / "artifacts" / "pruning_model.joblib")
    question = "Which risk profile is recorded for our client Curtis Brown?"
    ctx = CallContext(
        agent_id="a",
        public_name="wealth__get_client",
        server="wealth",
        tool="get_client",
        arguments={},
        meta={"aegis/question": question},
    )
    data = {
        "client_id": "CL-1",
        "name": "Curtis Brown",
        "risk_profile": "conservative",
        "notes": "x" * 300,
        "created_at": "2020-01-01",
        "employer": "ACME",
    }
    drop = await pruner.drop_paths(ctx, data)
    assert "risk_profile" not in drop
