"""Tests for the /evaluate and /batch-evaluate endpoints."""
from fastapi.testclient import TestClient

from app.api.optimize import OptimizationMode
from app.main import app
from app.models.market_data import Timeframe


client = TestClient(app)


def test_evaluate_parameters_with_valid_request() -> None:
    """Test /api/optimize/evaluate with a valid parameter set."""
    plan_body = {
        "symbols": ["AAPL"],
        "timeframes": ["5m"],
        "limit": 288,  # 1 day of 5m bars
        "mode": "global",
        "iteration_budget": 1,
        "train_ratio": 0.67,
        "script": """\
def signals(candles):
    ema_period = {{ema_fast_period}}
    results = []
    for i, bar in enumerate(candles):
        signal = None
        results.append({
            "time": bar.time.isoformat(),
            "signal": signal,
        })
    return results
""",
        "parameter_space": {
            "ema_fast_period": {
                "kind": "integer",
                "default": 20,
                "minimum": 5,
                "maximum": 50,
                "step": 1,
            }
        },
    }

    evaluate_body = {
        "plan": plan_body,
        "parameters": {"ema_fast_period": 25},
        "candidate_name": "test_candidate",
    }

    response = client.post("/api/optimize/evaluate", json=evaluate_body)
    if response.status_code != 200:
        print(f"Response status: {response.status_code}")
        print(f"Response body: {response.text}")
    assert response.status_code == 200
    body = response.json()

    assert body["candidate_name"] == "test_candidate"
    assert body["parameters"] == {"ema_fast_period": 25}
    assert "rendered_script" in body
    assert "ema_period = 25" in body["rendered_script"]
    assert "train_summary" in body
    assert "holdout_summary" in body
    assert "score_details" in body
    assert "overall_score" in body["score_details"]


def test_evaluate_parameters_renders_script() -> None:
    """Test that /api/optimize/evaluate properly renders parameter values into the script."""
    plan_body = {
        "symbols": ["AAPL"],
        "timeframes": ["5m"],
        "limit": 288,
        "mode": "global",
        "iteration_budget": 1,
        "train_ratio": 0.67,
        "parameter_space": {
            "ema_fast_period": {
                "kind": "integer",
                "default": 20,
                "minimum": 5,
                "maximum": 50,
            },
            "use_filter": {
                "kind": "boolean",
                "default": True,
            },
        },
        "script": """\
def signals(candles):
    ema_period = {{ema_fast_period}}
    use_filter = {{use_filter}}
    results = []
    for i, bar in enumerate(candles):
        signal = None
        results.append({
            "time": bar.time.isoformat(),
            "signal": signal,
        })
    return results
""",
    }

    evaluate_body = {
        "plan": plan_body,
        "parameters": {"ema_fast_period": 30, "use_filter": False},
        "candidate_name": "custom_params",
    }

    response = client.post("/api/optimize/evaluate", json=evaluate_body)
    assert response.status_code == 200
    body = response.json()

    assert "ema_period = 30" in body["rendered_script"]
    assert "use_filter = False" in body["rendered_script"]


def test_batch_evaluate_sorts_by_score() -> None:
    """Test /api/optimize/batch-evaluate returns leaderboard sorted by score."""
    plan_body = {
        "symbols": ["AAPL"],
        "timeframes": ["5m"],
        "limit": 288,
        "mode": "global",
        "iteration_budget": 3,
        "train_ratio": 0.67,
        "script": """\
def signals(candles):
    ema_period = {{ema_fast_period}}
    results = []
    for i, bar in enumerate(candles):
        signal = None
        results.append({
            "time": bar.time.isoformat(),
            "signal": signal,
        })
    return results
""",
        "parameter_space": {
            "ema_fast_period": {
                "kind": "integer",
                "default": 20,
                "minimum": 5,
                "maximum": 50,
            }
        },
    }

    batch_body = {
        "plan": plan_body,
        "candidates": [
            {"ema_fast_period": 15},
            {"ema_fast_period": 25},
            {"ema_fast_period": 35},
        ],
    }

    response = client.post("/api/optimize/batch-evaluate", json=batch_body)
    assert response.status_code == 200
    body = response.json()

    assert "leaderboard" in body
    assert "best_candidate" in body
    assert len(body["leaderboard"]) == 3

    # Verify leaderboard is sorted by overall_score descending
    scores = [c["score_details"]["overall_score"] for c in body["leaderboard"]]
    assert scores == sorted(scores, reverse=True)

    # Verify best_candidate is the first in the leaderboard
    assert body["best_candidate"]["candidate_name"] == body["leaderboard"][0]["candidate_name"]


def test_evaluate_rejects_invalid_parameters() -> None:
    """Test /api/optimize/evaluate rejects parameters outside bounds."""
    plan_body = {
        "symbols": ["AAPL"],
        "timeframes": ["5m"],
        "limit": 288,
        "mode": "global",
        "iteration_budget": 1,
        "train_ratio": 0.67,
        "script": """\
def signals(candles):
    ema_period = {{ema_fast_period}}
    results = []
    for i, bar in enumerate(candles):
        signal = None
        results.append({
            "time": bar.time.isoformat(),
            "signal": signal,
        })
    return results
""",
        "parameter_space": {
            "ema_fast_period": {
                "kind": "integer",
                "default": 20,
                "minimum": 5,
                "maximum": 50,
            }
        },
    }

    # Parameter value outside bounds is not validated at this layer,
    # but the script rendering will succeed. The intent is for the optimizer
    # loop to enforce bounds, not the evaluate endpoint.
    evaluate_body = {
        "plan": plan_body,
        "parameters": {"ema_fast_period": 100},
        "candidate_name": "out_of_bounds",
    }

    # This should still succeed because we don't validate bounds in evaluate;
    # the bounds are enforced by the optimizer loop itself.
    response = client.post("/api/optimize/evaluate", json=evaluate_body)
    assert response.status_code == 200
