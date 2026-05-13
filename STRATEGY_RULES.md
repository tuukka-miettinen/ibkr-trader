# `on_tick(state)` Strategy Script Rules

## Script Structure

- Must define `def on_tick(state):` — called every 5 seconds
- Must return a dict: `{"signal": "buy"}`, `{"signal": "sell"}`, or `{"signal": None}`
- Optional: set `STRATEGY_NAME = "My Strategy"` at module level
- `ta` (indicators) and `Candle` are pre-injected — no imports needed

## `state` Object (TickState dataclass) — use dot access

| Field                  | Type                      | Description                          |
| ---------------------- | ------------------------- | ------------------------------------ |
| `state.tick`           | `Candle`                  | Current 5-second bar                 |
| `state.candles`        | `dict[str, list[Candle]]` | Completed candles by timeframe       |
| `state.current_candles`| `dict[str, Candle\|None]` | In-progress (unfinished) candles     |
| `state.closed`         | `dict[str, Candle\|None]` | Candle that **just closed** this tick |
| `state.position`       | `PositionInfo\|None`      | Current position (None if flat)      |
| `state.cash`           | `float`                   | Available cash                       |
| `state.portfolio_value`| `float`                   | Cash + market value                  |
| `state.strategy`       | `dict`                    | Persistent state across ticks (r/w)  |

## Timeframe Keys (strings)

`"1m"`, `"5m"`, `"15m"` — used for `state.candles`, `state.closed`, `state.current_candles`

```python
closed_5m = state.closed.get("5m")        # Candle or None
candles_5m = state.candles.get("5m", [])   # list[Candle]
```

## Candle Object — dot access, not dict

```python
candle.time      # datetime
candle.open      # float
candle.high      # float
candle.low       # float
candle.close     # float
candle.volume    # int
```

```python
# ✅ Correct
price = candles_5m[-1].close

# ❌ Wrong — Candle is a Pydantic model, not a dict
price = candles_5m[-1]["close"]
```

## PositionInfo — dot access, not dict

```python
state.position.shares          # float — current shares held
state.position.avg_price       # float — average entry price
state.position.total_cost      # float — total cost basis
state.position.entries         # list[dict] — each: {"time", "price", "shares", "cost"}
state.position.unrealized_pnl  # float — current unrealized P&L
```

```python
# ✅ Correct
avg = state.position.avg_price

# ❌ Wrong
avg = state.position["avg_price"]
```

## Available Indicators (`ta.xxx`)

All take a `list[Candle]` and return a same-length list (leading `None`s for insufficient data):

```python
ta.sma(candles, period)              # Simple Moving Average
ta.ema(candles, period)              # Exponential Moving Average
ta.rsi(candles, period=14)           # RSI (0–100)
ta.vwap(candles)                     # VWAP (resets daily)
ta.atr(candles, period=14)           # Average True Range
ta.bollinger(candles, period, mult)  # → list[dict|None], keys: upper, mid, lower
ta.macd(candles, fast, slow, signal) # → list[dict|None], keys: macd, signal, hist
```

## Signal Return

```python
return {"signal": "buy", "size": 1.0}   # size is fraction of position_size (default 1.0)
return {"signal": "sell"}                # sells entire position
return {"signal": None}                  # do nothing
```

## Persistent State

```python
# Store values across ticks — survives between calls
state.strategy["my_counter"] = state.strategy.get("my_counter", 0) + 1
state.strategy["highest_price"] = max(state.strategy.get("highest_price", 0), price)
```

Must be JSON-serializable (no datetime objects, no Candle objects).

## Common Pattern

```python
STRATEGY_NAME = "Example RSI + VWAP"

def on_tick(state):
    # Only act on 5m candle close
    closed_5m = state.closed.get("5m")
    if closed_5m is None:
        return {"signal": None}

    candles_5m = state.candles.get("5m", [])
    if len(candles_5m) < 20:
        return {"signal": None}

    rsi = ta.rsi(candles_5m, 14)
    vwap = ta.vwap(candles_5m)

    # Buy logic — check state.position is None
    if state.position is None:
        if rsi[-1] is not None and rsi[-1] < 30 and candles_5m[-1].close < vwap[-1]:
            return {"signal": "buy"}

    # Sell logic — check state.position is not None
    if state.position is not None:
        profit = (candles_5m[-1].close - state.position.avg_price) / state.position.avg_price
        if profit >= 0.02:
            return {"signal": "sell"}

    return {"signal": None}
```

## Session Config (set when creating a session)

| Setting              | Default  | Description                                |
| -------------------- | -------- | ------------------------------------------ |
| `position_size`      | $1,000   | Dollar amount per buy                      |
| `max_entries`        | 5        | Max concurrent buys before selling         |
| `max_daily_loss`     | $500     | Session stops if exceeded                  |
| `capital_per_symbol` | $10,000  | Starting cash per symbol                   |
