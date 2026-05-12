"""Tests for optimization job API endpoints."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.api.optimize import OptimizationJobStatus
from app.main import app
from app.models.market_data import Timeframe

client = TestClient(app)


def test_start_optimization_creates_job() -> None:
    """Test POST /api/optimize/start creates a job and returns job_id."""
    request_body = {
        "plan": {
            "symbols": ["AAPL"],
            "timeframes": ["5m"],
            "limit": 288,
            "mode": "global",
            "iteration_budget": 2,
            "train_ratio": 0.67,
            "script": """\
def signals(candles):
    ema_period = {{ema_period}}
    results = []
    for i, bar in enumerate(candles):
        signal = None
        results.append({"time": bar.time.isoformat(), "signal": signal})
    return results
""",
            "parameter_space": {
                "ema_period": {
                    "kind": "integer",
                    "default": 20,
                    "minimum": 5,
                    "maximum": 50,
                }
            },
        },
        "provider": "fake",
    }
    
    response = client.post("/api/optimize/start", json=request_body)
    
    assert response.status_code == 200
    body = response.json()
    assert "job_id" in body
    assert body["status"] == OptimizationJobStatus.QUEUED
    assert len(body["job_id"]) > 0


def test_start_optimization_requires_valid_script() -> None:
    """Test POST /api/optimize/start rejects invalid script."""
    request_body = {
        "plan": {
            "symbols": ["AAPL"],
            "timeframes": ["5m"],
            "limit": 288,
            "mode": "global",
            "iteration_budget": 1,
            "train_ratio": 0.67,
            "script": "invalid python",  # Not a valid signals function
            "parameter_space": {
                "param": {
                    "kind": "integer",
                    "default": 20,
                    "minimum": 5,
                    "maximum": 50,
                }
            },
        },
        "provider": "fake",
    }
    
    response = client.post("/api/optimize/start", json=request_body)
    
    # Invalid script returns 400 or 422
    assert response.status_code in [400, 422]


def test_get_optimization_job_returns_status() -> None:
    """Test GET /api/optimize/{job_id} returns job status."""
    # Start a job first
    request_body = {
        "plan": {
            "symbols": ["AAPL"],
            "timeframes": ["5m"],
            "limit": 288,
            "mode": "global",
            "iteration_budget": 1,
            "train_ratio": 0.67,
            "script": """\
def signals(candles):
    ema_period = {{ema_period}}
    results = []
    for i, bar in enumerate(candles):
        signal = None
        results.append({"time": bar.time.isoformat(), "signal": signal})
    return results
""",
            "parameter_space": {
                "ema_period": {
                    "kind": "integer",
                    "default": 20,
                    "minimum": 5,
                    "maximum": 50,
                }
            },
        },
        "provider": "fake",
    }
    
    start_response = client.post("/api/optimize/start", json=request_body)
    job_id = start_response.json()["job_id"]
    
    # Give background task a moment to start
    time.sleep(0.5)
    
    # Get job status
    response = client.get(f"/api/optimize/{job_id}")
    
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert "status" in body
    assert "leaderboard" in body
    assert "created_at" in body


def test_get_optimization_job_not_found() -> None:
    """Test GET /api/optimize/{job_id} returns 404 for unknown job."""
    response = client.get("/api/optimize/nonexistent-job-id")
    
    assert response.status_code == 404


def test_start_optimization_rejects_unknown_provider() -> None:
    """Test POST /api/optimize/start rejects unknown LLM provider."""
    request_body = {
        "plan": {
            "symbols": ["AAPL"],
            "timeframes": ["5m"],
            "limit": 288,
            "mode": "global",
            "iteration_budget": 1,
            "train_ratio": 0.67,
            "script": """\
def signals(candles):
    results = []
    for i, bar in enumerate(candles):
        results.append({"time": bar.time.isoformat(), "signal": None})
    return results
""",
            "parameter_space": {},
        },
        "provider": "unknown_provider",
    }
    
    response = client.post("/api/optimize/start", json=request_body)
    
    # Empty parameter_space triggers Pydantic validation error (422)
    # Actually calling with unknown provider would trigger our 400 error
    # Let's test with valid parameter_space but unknown provider
    request_body2 = {
        "plan": {
            "symbols": ["AAPL"],
            "timeframes": ["5m"],
            "limit": 288,
            "mode": "global",
            "iteration_budget": 1,
            "train_ratio": 0.67,
            "script": """\
def signals(candles):
    results = []
    for i, bar in enumerate(candles):
        results.append({"time": bar.time.isoformat(), "signal": None})
    return results
""",
            "parameter_space": {
                "param": {
                    "kind": "integer",
                    "default": 20,
                    "minimum": 5,
                    "maximum": 50,
                }
            },
        },
        "provider": "unknown_provider",
    }
    
    response = client.post("/api/optimize/start", json=request_body2)
    
    assert response.status_code == 400
    assert "Unknown LLM provider" in response.text


def test_optimization_job_eventually_completes() -> None:
    """Test that optimization job eventually completes (may take a moment)."""
    request_body = {
        "plan": {
            "symbols": ["AAPL"],
            "timeframes": ["5m"],
            "limit": 288,
            "mode": "global",
            "iteration_budget": 1,
            "train_ratio": 0.67,
            "script": """\
def signals(candles):
    ema_period = {{ema_period}}
    results = []
    for i, bar in enumerate(candles):
        signal = None
        results.append({"time": bar.time.isoformat(), "signal": signal})
    return results
""",
            "parameter_space": {
                "ema_period": {
                    "kind": "integer",
                    "default": 20,
                    "minimum": 5,
                    "maximum": 50,
                }
            },
        },
        "provider": "fake",
    }
    
    start_response = client.post("/api/optimize/start", json=request_body)
    job_id = start_response.json()["job_id"]
    
    # Poll until completed (with timeout)
    max_attempts = 30
    for attempt in range(max_attempts):
        response = client.get(f"/api/optimize/{job_id}")
        body = response.json()
        
        if body["status"] == OptimizationJobStatus.COMPLETED:
            assert "best_candidate" in body
            assert body["iterations_completed"] > 0
            assert "leaderboard" in body
            assert len(body["leaderboard"]) > 0
            break
        
        if body["status"] == OptimizationJobStatus.FAILED:
            pytest.fail(f"Job failed: {body.get('error_message')}")
        
        time.sleep(0.5)
    else:
        pytest.fail(f"Job did not complete after {max_attempts} attempts")
