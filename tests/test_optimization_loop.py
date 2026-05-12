"""Tests for the optimization loop."""
import pytest

from app.api.optimize import OptimizationMode, ParameterKind, ParameterSpec, OptimizationRequest
from app.llm.provider import FakeLLMProvider
from app.models.market_data import Timeframe
from app.services.optimization_loop import run_optimization_loop


@pytest.mark.asyncio
async def test_optimization_loop_runs_iterations() -> None:
    """Test optimization loop runs specified number of iterations."""
    plan = OptimizationRequest(
        symbols=["AAPL"],
        timeframes=[Timeframe.FIVE_MINUTES],
        limit=288,
        mode=OptimizationMode.GLOBAL,
        iteration_budget=3,
        train_ratio=0.67,
        script="""\
def signals(candles):
    ema_period = {{ema_period}}
    results = []
    for i, bar in enumerate(candles):
        signal = None
        results.append({"time": bar.time.isoformat(), "signal": signal})
    return results
""",
        parameter_space={
            "ema_period": ParameterSpec(
                kind=ParameterKind.INTEGER,
                default=20,
                minimum=5,
                maximum=50,
            ),
        },
    )
    
    llm = FakeLLMProvider()
    
    result = await run_optimization_loop(plan, llm)
    
    assert result["iterations_completed"] == 3
    assert len(result["leaderboard"]) == 3


@pytest.mark.asyncio
async def test_optimization_loop_returns_best_candidate() -> None:
    """Test optimization loop returns best candidate with highest score."""
    plan = OptimizationRequest(
        symbols=["AAPL"],
        timeframes=[Timeframe.FIVE_MINUTES],
        limit=288,
        mode=OptimizationMode.GLOBAL,
        iteration_budget=3,
        train_ratio=0.67,
        script="""\
def signals(candles):
    ema_period = {{ema_period}}
    results = []
    for i, bar in enumerate(candles):
        signal = None
        results.append({"time": bar.time.isoformat(), "signal": signal})
    return results
""",
        parameter_space={
            "ema_period": ParameterSpec(
                kind=ParameterKind.INTEGER,
                default=20,
                minimum=5,
                maximum=50,
            ),
        },
    )
    
    llm = FakeLLMProvider()
    
    result = await run_optimization_loop(plan, llm)
    
    assert result["best_candidate"] is not None
    assert "score_details" in result["best_candidate"]
    assert "overall_score" in result["best_candidate"]["score_details"]


@pytest.mark.asyncio
async def test_optimization_loop_leaderboard_is_sorted() -> None:
    """Test optimization loop returns leaderboard sorted by score descending."""
    plan = OptimizationRequest(
        symbols=["AAPL"],
        timeframes=[Timeframe.FIVE_MINUTES],
        limit=288,
        mode=OptimizationMode.GLOBAL,
        iteration_budget=5,
        train_ratio=0.67,
        script="""\
def signals(candles):
    ema_period = {{ema_period}}
    results = []
    for i, bar in enumerate(candles):
        signal = None
        results.append({"time": bar.time.isoformat(), "signal": signal})
    return results
""",
        parameter_space={
            "ema_period": ParameterSpec(
                kind=ParameterKind.INTEGER,
                default=20,
                minimum=5,
                maximum=50,
            ),
        },
    )
    
    llm = FakeLLMProvider()
    
    result = await run_optimization_loop(plan, llm)
    
    leaderboard = result["leaderboard"]
    scores = [c["score_details"]["overall_score"] for c in leaderboard]
    
    # Check descending order
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_optimization_loop_detects_plateau() -> None:
    """Test optimization loop stops early on plateau."""
    plan = OptimizationRequest(
        symbols=["AAPL"],
        timeframes=[Timeframe.FIVE_MINUTES],
        limit=288,
        mode=OptimizationMode.GLOBAL,
        iteration_budget=100,  # High budget but expect early stop
        train_ratio=0.67,
        script="""\
def signals(candles):
    ema_period = {{ema_period}}
    results = []
    for i, bar in enumerate(candles):
        signal = None
        results.append({"time": bar.time.isoformat(), "signal": signal})
    return results
""",
        parameter_space={
            "ema_period": ParameterSpec(
                kind=ParameterKind.INTEGER,
                default=20,
                minimum=5,
                maximum=50,
            ),
        },
    )
    
    llm = FakeLLMProvider()
    
    result = await run_optimization_loop(plan, llm)
    
    # With fake provider incrementing by 1, plateau detection should kick in
    # (all candidates get similar scores with no improvement)
    assert result["iterations_completed"] < plan.iteration_budget
    assert result["early_stop_reason"] == "plateau"


@pytest.mark.asyncio
async def test_optimization_loop_calls_status_callback() -> None:
    """Test optimization loop calls on_status_update callback."""
    plan = OptimizationRequest(
        symbols=["AAPL"],
        timeframes=[Timeframe.FIVE_MINUTES],
        limit=288,
        mode=OptimizationMode.GLOBAL,
        iteration_budget=2,
        train_ratio=0.67,
        script="""\
def signals(candles):
    ema_period = {{ema_period}}
    results = []
    for i, bar in enumerate(candles):
        signal = None
        results.append({"time": bar.time.isoformat(), "signal": signal})
    return results
""",
        parameter_space={
            "ema_period": ParameterSpec(
                kind=ParameterKind.INTEGER,
                default=20,
                minimum=5,
                maximum=50,
            ),
        },
    )
    
    llm = FakeLLMProvider()
    
    updates = []
    
    def capture_status(update):
        updates.append(update)
    
    await run_optimization_loop(plan, llm, on_status_update=capture_status)
    
    # Should have received updates for each iteration
    assert len(updates) >= 2
    assert all("status" in u for u in updates)
    assert all("iteration" in u for u in updates)
