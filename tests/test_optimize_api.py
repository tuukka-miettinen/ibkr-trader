from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_optimize_plan_accepts_global_request() -> None:
    response = client.post(
        "/api/optimize/plan",
        json={
            "symbols": ["AAPL", "MSFT", "AAPL"],
            "timeframes": ["5m", "15m"],
            "limit": 1638,
            "mode": "global",
            "iteration_budget": 10,
            "train_ratio": 0.67,
            "parameter_space": {
                "ema_fast_period": {
                    "kind": "integer",
                    "default": 20,
                    "minimum": 5,
                    "maximum": 50,
                    "step": 1,
                    "description": "Fast EMA length",
                    "allow_sector_override": True,
                },
                "use_macd_filter": {
                    "kind": "boolean",
                    "default": True,
                    "description": "Require MACD confirmation",
                },
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["symbols"] == ["AAPL", "MSFT"]
    assert body["parameter_names"] == ["ema_fast_period", "use_macd_filter"]
    assert any("No LLM calls" in note for note in body["notes"])


def test_optimize_plan_requires_sector_map_in_sector_mode() -> None:
    response = client.post(
        "/api/optimize/plan",
        json={
            "symbols": ["AAPL", "MSFT"],
            "mode": "sector",
            "parameter_space": {
                "rsi_reclaim_level": {
                    "kind": "integer",
                    "default": 50,
                    "minimum": 30,
                    "maximum": 70,
                }
            },
            "sector_map": {"AAPL": "technology"},
        },
    )

    assert response.status_code == 422
    assert "sector_map is missing symbols" in response.text


def test_optimize_plan_rejects_invalid_script() -> None:
    response = client.post(
        "/api/optimize/plan",
        json={
            "symbols": ["AAPL"],
            "parameter_space": {
                "ema_fast_period": {
                    "kind": "integer",
                    "default": 20,
                    "minimum": 5,
                    "maximum": 50,
                }
            },
            "script": "def broken(:\n    pass\n",
        },
    )

    assert response.status_code == 400
    assert "Syntax error in script" in response.text