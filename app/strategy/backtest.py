from __future__ import annotations

from dataclasses import dataclass, field

from app.models.market_data import Candle


@dataclass
class BacktestConfig:
    starting_capital: float = 10000.0
    position_size: float = 1000.0
    max_entries: int = 5


@dataclass
class Trade:
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    shares: float = 0.0
    total_cost: float = 0.0
    dollar_pnl: float = 0.0
    entries: list[dict] = field(default_factory=list)


@dataclass
class DailySnapshot:
    date: str
    realized_trades: int = 0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    position_shares: float = 0.0
    position_cost: float = 0.0
    day_close_price: float = 0.0
    win_rate: float = 0.0


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    daily_snapshots: list[DailySnapshot] = field(default_factory=list)
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    win_rate: float = 0.0
    num_trades: int = 0
    starting_capital: float = 10000.0
    final_balance: float = 10000.0
    total_dollar_pnl: float = 0.0


def _normalize_config(config: BacktestConfig | None) -> BacktestConfig:
    if config is None:
        return BacktestConfig()

    return BacktestConfig(
        starting_capital=config.starting_capital if config.starting_capital > 0 else 10000.0,
        position_size=config.position_size if config.position_size > 0 else 1000.0,
        max_entries=max(1, int(config.max_entries)),
    )


def run(candles: list[Candle], sigs: list[dict], config: BacktestConfig | None = None) -> BacktestResult:
    resolved_config = _normalize_config(config)
    trades: list[Trade] = []
    daily_snapshots: list[DailySnapshot] = []
    cash_balance = resolved_config.starting_capital
    position_entries: list[dict] = []
    position_shares = 0.0
    position_cost = 0.0

    # Per-day tracking
    current_date: str | None = None
    day_realized_trades: list[Trade] = []

    def _flush_day(date: str, close_price: float) -> None:
        unrealized = (position_shares * close_price - position_cost) if position_shares > 0 else 0.0
        realized_pnl = round(sum(t.dollar_pnl for t in day_realized_trades), 4)
        wins = sum(1 for t in day_realized_trades if t.dollar_pnl > 0)
        daily_snapshots.append(DailySnapshot(
            date=date,
            realized_trades=len(day_realized_trades),
            realized_pnl=realized_pnl,
            unrealized_pnl=round(unrealized, 4),
            position_shares=round(position_shares, 8),
            position_cost=round(position_cost, 4),
            day_close_price=round(close_price, 4),
            win_rate=round(wins / len(day_realized_trades) * 100, 1) if day_realized_trades else 0.0,
        ))

    for bar, sig in zip(candles, sigs):
        bar_date = bar.time.isoformat().split("T", 1)[0]

        if current_date is not None and bar_date != current_date:
            # Day boundary crossed — flush the previous day
            _flush_day(current_date, prev_close)
            day_realized_trades = []

        current_date = bar_date
        prev_close = bar.close

        signal = sig.get("signal")
        if signal == "buy":
            if (
                bar.close > 0
                and cash_balance >= resolved_config.position_size
                and len(position_entries) < resolved_config.max_entries
            ):
                shares = resolved_config.position_size / bar.close
                cost = shares * bar.close
                cash_balance -= cost
                position_cost += cost
                position_shares += shares
                position_entries.append({
                    "time": sig["time"],
                    "price": round(bar.close, 4),
                    "shares": round(shares, 8),
                    "cost": round(cost, 4),
                })
        elif signal == "sell" and position_shares > 0:
            proceeds = position_shares * bar.close
            dollar_pnl = proceeds - position_cost
            avg_entry_price = position_cost / position_shares
            pnl_pct = dollar_pnl / position_cost * 100 if position_cost else 0.0
            trade = Trade(
                entry_time=position_entries[0]["time"],
                exit_time=sig["time"],
                entry_price=round(avg_entry_price, 4),
                exit_price=round(bar.close, 4),
                pnl=round(dollar_pnl, 4),
                pnl_pct=round(pnl_pct, 4),
                shares=round(position_shares, 8),
                total_cost=round(position_cost, 4),
                dollar_pnl=round(dollar_pnl, 4),
                entries=position_entries.copy(),
            )
            trades.append(trade)
            day_realized_trades.append(trade)
            cash_balance += proceeds
            position_entries = []
            position_shares = 0.0
            position_cost = 0.0

    # Flush the last day
    if current_date is not None and candles:
        _flush_day(current_date, candles[-1].close)

    final_market_value = position_shares * candles[-1].close if candles and position_shares > 0 else 0.0
    final_balance = round(cash_balance + final_market_value, 4)
    total_pnl = round(final_balance - resolved_config.starting_capital, 4)
    wins = sum(1 for t in trades if t.dollar_pnl > 0)
    return BacktestResult(
        trades=trades,
        daily_snapshots=daily_snapshots,
        total_pnl=total_pnl,
        total_pnl_pct=round(total_pnl / resolved_config.starting_capital * 100, 4),
        win_rate=round(wins / len(trades) * 100, 1) if trades else 0.0,
        num_trades=len(trades),
        starting_capital=round(resolved_config.starting_capital, 4),
        final_balance=final_balance,
        total_dollar_pnl=total_pnl,
    )
