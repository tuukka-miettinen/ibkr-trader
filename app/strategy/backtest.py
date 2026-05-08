from __future__ import annotations

from dataclasses import dataclass, field

from app.models.market_data import Candle


@dataclass
class Trade:
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    win_rate: float = 0.0
    num_trades: int = 0


def run(candles: list[Candle], sigs: list[dict]) -> BacktestResult:
    trades: list[Trade] = []
    entry_price: float | None = None
    entry_time: str | None = None

    for bar, sig in zip(candles, sigs):
        if sig["signal"] == "buy" and entry_price is None:
            entry_price = bar.close
            entry_time = sig["time"]
        elif sig["signal"] == "sell" and entry_price is not None:
            pnl = bar.close - entry_price
            pnl_pct = pnl / entry_price * 100
            trades.append(Trade(
                entry_time=entry_time,
                exit_time=sig["time"],
                entry_price=entry_price,
                exit_price=bar.close,
                pnl=round(pnl, 4),
                pnl_pct=round(pnl_pct, 4),
            ))
            entry_price = None
            entry_time = None

    total_pnl = round(sum(t.pnl for t in trades), 4)
    wins = sum(1 for t in trades if t.pnl > 0)
    return BacktestResult(
        trades=trades,
        total_pnl=total_pnl,
        total_pnl_pct=round(sum(t.pnl_pct for t in trades), 4),
        win_rate=round(wins / len(trades) * 100, 1) if trades else 0.0,
        num_trades=len(trades),
    )
