"""Replay captured live paper-session ticks and compare them to live trades."""
from __future__ import annotations

import math
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.live import LiveRepository
from app.db.strategies import StrategyRepository
from app.models.market_data import Candle, Timeframe
from app.services.live_engine import CANDLE_TIMEFRAMES
from app.strategy.sandbox import compile_tick_script
from app.strategy.tick_backtest import TickBacktestConfig, run_tick_backtest


class LiveComparisonService:
    def __init__(self) -> None:
        self._live_repo = LiveRepository()
        self._strategy_repo = StrategyRepository()

    async def compare_session_symbol(
        self,
        session: AsyncSession,
        *,
        session_id: str,
        symbol: str,
        minutes: int = 30,
    ) -> dict:
        normalized_symbol = symbol.upper()
        live_session = await self._live_repo.get_session(session, session_id)
        if live_session is None:
            raise ValueError(f"Session {session_id} not found")

        session_symbol = await self._live_repo.get_session_symbol(session, session_id, normalized_symbol)
        if session_symbol is None:
            raise ValueError(f"Symbol {normalized_symbol} is not configured for session {session_id}")

        algo = await self._strategy_repo.get_algorithm(session, session_symbol.algorithm_id)
        if algo is None:
            raise ValueError(f"Algorithm {session_symbol.algorithm_id} not found for {normalized_symbol}")

        captured_ticks = await self._live_repo.get_ticks(session, session_symbol.id)
        if not captured_ticks:
            raise ValueError(f"No captured paper-session ticks found for {normalized_symbol}")

        captured_ticks = [self._with_symbol(tick, normalized_symbol) for tick in captured_ticks]
        start_time = captured_ticks[0].time
        requested_end = start_time + timedelta(minutes=minutes)
        window_ticks = [tick for tick in captured_ticks if tick.time <= requested_end]
        if not window_ticks:
            raise ValueError(f"No captured paper-session ticks found in the requested window for {normalized_symbol}")
        end_time = window_ticks[-1].time

        seed_candles = await self._live_repo.get_seed_candles(session, session_symbol.id)
        replay_seed_candles = {
            timeframe: [self._with_symbol(candle, normalized_symbol) for candle in candles]
            for timeframe, candles in seed_candles.items()
            if timeframe in CANDLE_TIMEFRAMES
        }

        live_trades = await self._live_repo.get_trades(
            session,
            session_id,
            symbol=normalized_symbol,
            start_time=start_time,
            end_time=end_time,
            ascending=True,
        )
        live_trade_data = [self._serialize_live_trade(trade) for trade in live_trades]

        replay_executions: list[dict] = []
        on_tick_fn = compile_tick_script(algo.script)
        config = TickBacktestConfig(
            starting_capital=session_symbol.allocated_capital,
            position_size=session_symbol.position_size,
            max_entries=session_symbol.max_entries,
            candle_timeframes=CANDLE_TIMEFRAMES,
            fee_per_share=0.0,
            fee_min_order=0.0,
            fee_max_pct=0.0,
        )
        replay_result = run_tick_backtest(
            window_ticks,
            on_tick_fn,
            config=config,
            seed_candles=replay_seed_candles,
            on_execution=replay_executions.append,
        )

        mismatches = self._compare_trade_sequences(live_trade_data, replay_executions)
        actual_minutes = round((end_time - start_time).total_seconds() / 60, 2)

        return {
            "session_id": session_id,
            "symbol": normalized_symbol,
            "strategy": {
                "algorithm_id": algo.id,
                "name": algo.name,
                "version": algo.version,
            },
            "window": {
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "requested_minutes": minutes,
                "actual_minutes": actual_minutes,
            },
            "captured_tick_count": len(window_ticks),
            "seed_counts": {
                timeframe.value: len(candles)
                for timeframe, candles in replay_seed_candles.items()
            },
            "live_trades": live_trade_data,
            "replay_trades": replay_executions,
            "matched": len(mismatches) == 0,
            "mismatch_count": len(mismatches),
            "mismatches": mismatches,
            "replay_summary": {
                "num_round_trips": replay_result.num_trades,
                "open_entries": replay_result.open_entries,
                "total_pnl": replay_result.total_pnl,
                "final_balance": replay_result.final_balance,
            },
        }

    def _serialize_live_trade(self, trade) -> dict:
        return {
            "side": trade.side,
            "time": trade.event_time.isoformat() if trade.event_time else None,
            "shares": round(trade.shares, 8),
            "price": round(trade.price, 4),
            "cost": round(trade.cost, 4),
            "pnl": round(trade.pnl, 4) if trade.pnl is not None else None,
            "pnl_pct": round(trade.pnl_pct, 4) if trade.pnl_pct is not None else None,
        }

    def _compare_trade_sequences(self, live_trades: list[dict], replay_trades: list[dict]) -> list[dict]:
        mismatches: list[dict] = []
        max_len = max(len(live_trades), len(replay_trades))
        for idx in range(max_len):
            live_trade = live_trades[idx] if idx < len(live_trades) else None
            replay_trade = replay_trades[idx] if idx < len(replay_trades) else None
            if live_trade is None or replay_trade is None:
                mismatches.append({
                    "index": idx,
                    "reason": "missing_trade",
                    "live": live_trade,
                    "replay": replay_trade,
                })
                continue

            diffs = []
            if live_trade["side"] != replay_trade["side"]:
                diffs.append("side")
            if live_trade["time"] != replay_trade["time"]:
                diffs.append("time")
            if not math.isclose(live_trade["shares"], replay_trade["shares"], rel_tol=0.0, abs_tol=1e-8):
                diffs.append("shares")
            if not math.isclose(live_trade["price"], replay_trade["price"], rel_tol=0.0, abs_tol=1e-4):
                diffs.append("price")

            if diffs:
                mismatches.append({
                    "index": idx,
                    "reason": "field_mismatch",
                    "fields": diffs,
                    "live": live_trade,
                    "replay": replay_trade,
                })

        return mismatches

    def _with_symbol(self, candle: Candle, symbol: str) -> Candle:
        return Candle(
            symbol=symbol,
            timeframe=candle.timeframe,
            time=candle.time,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
        )


live_comparison_service = LiveComparisonService()
