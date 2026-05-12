"""Tests for optimizer scoring functionality."""
from app.services.optimizer import score_candidate
from app.strategy import backtest as bt


def test_score_candidate_positive_both_windows() -> None:
    train_result = bt.BacktestResult(
        trades=[],
        total_pnl=100.0,
        total_pnl_pct=5.0,
        win_rate=60.0,
        num_trades=15,
    )
    holdout_result = bt.BacktestResult(
        trades=[],
        total_pnl=50.0,
        total_pnl_pct=2.5,
        win_rate=55.0,
        num_trades=12,
    )

    score = score_candidate("test", train_result, holdout_result, ["AAPL"])
    assert score["overall_score"] > 0.0
    assert score["consistency_bonus"] > 0.0


def test_score_candidate_low_trade_count_penalty() -> None:
    train_result = bt.BacktestResult(
        trades=[],
        total_pnl=50.0,
        total_pnl_pct=2.5,
        win_rate=100.0,
        num_trades=5,
    )
    holdout_result = bt.BacktestResult(
        trades=[],
        total_pnl=100.0,
        total_pnl_pct=5.0,
        win_rate=100.0,
        num_trades=2,
    )

    score = score_candidate("test", train_result, holdout_result, ["AAPL"])
    assert score["trade_count_component"] < 0.20


def test_score_candidate_mixed_windows() -> None:
    train_result = bt.BacktestResult(
        trades=[],
        total_pnl=100.0,
        total_pnl_pct=5.0,
        win_rate=60.0,
        num_trades=15,
    )
    holdout_result = bt.BacktestResult(
        trades=[],
        total_pnl=-50.0,
        total_pnl_pct=-2.5,
        win_rate=40.0,
        num_trades=10,
    )

    score = score_candidate("test", train_result, holdout_result, ["AAPL"])
    assert score["consistency_bonus"] == 0.0
    assert score["overall_score"] < 0.3


def test_score_candidate_includes_justification() -> None:
    train_result = bt.BacktestResult(
        trades=[],
        total_pnl=50.0,
        total_pnl_pct=2.5,
        win_rate=50.0,
        num_trades=15,
    )
    holdout_result = bt.BacktestResult(
        trades=[],
        total_pnl=30.0,
        total_pnl_pct=1.5,
        win_rate=45.0,
        num_trades=12,
    )

    score = score_candidate("test", train_result, holdout_result, ["AAPL"])
    assert "Holdout PnL" in score["justification"]
    assert "Win rate" in score["justification"]
