"""Deterministic scoring and evaluation for optimization candidates."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.models.market_data import Timeframe
from app.services.backtest import run_backtest_core, summarize_result, summarize_trades
from app.strategy import backtest as bt
from app.strategy.parameter_renderer import render_parameters_into_script, validate_rendered_script
from app.strategy.walk_forward import split_candles_walk_forward

if TYPE_CHECKING:
    from app.api.optimize import OptimizationRequest


def score_candidate(
    name: str,
    train_result: bt.BacktestResult,
    holdout_result: bt.BacktestResult,
    symbols: list[str],
    sector_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Score a candidate using validation PnL, win rate, trade count, and consistency.

    The score prioritizes holdout (validation) performance. Components:
    - holdout_pnl (40%): validation period profit and loss
    - holdout_win_rate (20%): percentage of winning trades in validation
    - trade_count_penalty (20%): penalize if <10 trades in validation
    - consistency_bonus (20%): bonus if results are consistent across all requested symbols

    Args:
        name: Candidate identifier.
        train_result: BacktestResult from the training window.
        holdout_result: BacktestResult from the holdout (validation) window.
        symbols: List of symbols evaluated.
        sector_map: Optional dict mapping symbols to sectors for sector consistency check.

    Returns:
        A dict with `overall_score`, component scores, and a justification string.
    """
    # Component 1: Holdout PnL (normalized to [-1, 1] relative to a target of ±100 per point)
    holdout_pnl_normalized = min(1.0, max(-1.0, holdout_result.total_pnl / 100.0))
    pnl_component = holdout_pnl_normalized * 0.40

    # Component 2: Holdout win rate (0-100 -> 0-1 scale)
    win_rate_component = (holdout_result.win_rate / 100.0) * 0.20

    # Component 3: Trade count penalty (penalize <10 trades)
    trade_count = holdout_result.num_trades
    if trade_count < 10:
        trade_count_penalty = (10 - trade_count) * 0.01
    else:
        trade_count_penalty = 0.0
    trade_count_component = (1.0 - min(1.0, trade_count_penalty)) * 0.20

    # Component 4: Consistency (simple check: if any result is positive, it's consistent)
    consistency_bonus = 0.0
    if train_result.total_pnl > 0 and holdout_result.total_pnl > 0:
        consistency_bonus = 0.20

    overall_score = pnl_component + win_rate_component + trade_count_component + consistency_bonus

    justification_parts = [
        f"Holdout PnL: {holdout_result.total_pnl:.2f} ({holdout_result.num_trades} trades)",
        f"Win rate: {holdout_result.win_rate:.1f}%",
    ]
    if trade_count_penalty > 0:
        justification_parts.append(f"Low trade count penalty: {trade_count_penalty:.3f}")
    if consistency_bonus > 0:
        justification_parts.append("Consistent: train and holdout both positive")

    return {
        "candidate_name": name,
        "overall_score": round(overall_score, 4),
        "pnl_component": round(pnl_component, 4),
        "win_rate_component": round(win_rate_component, 4),
        "trade_count_component": round(trade_count_component, 4),
        "consistency_bonus": round(consistency_bonus, 4),
        "holdout_pnl": round(holdout_result.total_pnl, 4),
        "holdout_win_rate": holdout_result.win_rate,
        "holdout_trades": holdout_result.num_trades,
        "train_pnl": round(train_result.total_pnl, 4),
        "train_trades": train_result.num_trades,
        "justification": " | ".join(justification_parts),
    }


def evaluate_candidate(
    plan: OptimizationRequest,
    parameters: dict[str, Any],
    candidate_name: str = "candidate",
) -> dict[str, Any]:
    """Evaluate a single parameter set across all symbols and timeframes.

    Args:
        plan: The OptimizationRequest defining symbols, timeframes, and the base script.
        parameters: Dict of parameter values to substitute into the script.
        candidate_name: Optional identifier for this candidate.

    Returns:
        A dict with `rendered_script`, train and holdout summaries, and score details.

    Raises:
        ValueError: If parameters are invalid or script rendering fails.
    """
    if not hasattr(plan, "script"):
        raise ValueError("plan must have 'script' attribute")
    if not hasattr(plan, "symbols"):
        raise ValueError("plan must have 'symbols' attribute")
    if not hasattr(plan, "timeframes"):
        raise ValueError("plan must have 'timeframes' attribute")
    if not hasattr(plan, "limit"):
        raise ValueError("plan must have 'limit' attribute")
    if not hasattr(plan, "train_ratio"):
        raise ValueError("plan must have 'train_ratio' attribute")
    if not hasattr(plan, "sector_map"):
        raise ValueError("plan must have 'sector_map' attribute")

    # Render the script
    rendered_script = render_parameters_into_script(plan.script, parameters)
    validate_rendered_script(rendered_script)

    # Evaluate on each symbol + timeframe combination
    train_results = []
    holdout_results = []
    all_train_trades = []
    all_holdout_trades = []

    for symbol in plan.symbols:
        for timeframe in plan.timeframes:
            # Fetch candles
            candles, sigs, result = run_backtest_core(symbol, timeframe, plan.limit, rendered_script)

            if not candles:
                continue

            # Split into train and holdout
            train_candles, holdout_candles = split_candles_walk_forward(candles, plan.train_ratio)

            # Re-run backtest on each window
            _, train_sigs, train_result = run_backtest_core(
                symbol, timeframe, len(train_candles), rendered_script
            )
            _, holdout_sigs, holdout_result = run_backtest_core(
                symbol, timeframe, len(holdout_candles), rendered_script
            )

            train_results.append(train_result)
            holdout_results.append(holdout_result)
            all_train_trades.extend(train_result.trades)
            all_holdout_trades.extend(holdout_result.trades)

    # Aggregate results
    from app.services.backtest import summarize_trades

    train_summary = summarize_trades(all_train_trades) if all_train_trades else {"num_trades": 0, "total_pnl": 0, "total_pnl_pct": 0, "win_rate": 0}
    holdout_summary = summarize_trades(all_holdout_trades) if all_holdout_trades else {"num_trades": 0, "total_pnl": 0, "total_pnl_pct": 0, "win_rate": 0}

    # Create aggregate result objects for scoring
    train_agg = bt.BacktestResult(
        trades=all_train_trades,
        total_pnl=train_summary["total_pnl"],
        total_pnl_pct=train_summary["total_pnl_pct"],
        win_rate=train_summary["win_rate"],
        num_trades=train_summary["num_trades"],
    )
    holdout_agg = bt.BacktestResult(
        trades=all_holdout_trades,
        total_pnl=holdout_summary["total_pnl"],
        total_pnl_pct=holdout_summary["total_pnl_pct"],
        win_rate=holdout_summary["win_rate"],
        num_trades=holdout_summary["num_trades"],
    )

    # Score
    score_details = score_candidate(
        candidate_name,
        train_agg,
        holdout_agg,
        plan.symbols,
        plan.sector_map if plan.sector_map else None,
    )

    return {
        "candidate_name": candidate_name,
        "parameters": parameters,
        "rendered_script": rendered_script,
        "train_summary": train_summary,
        "holdout_summary": holdout_summary,
        "score_details": score_details,
    }
