STRATEGY_NAME = "Paper Trading Test — Buy/Sell Every Minute"

def on_tick(state):
    """Buy on every closed 1m candle when flat, sell on the next closed 1m candle."""
    closed_1m = state.closed.get("1m")

    if closed_1m is None:
        return {"signal": None, "strategy_name": STRATEGY_NAME}

    candles_1m = state.candles.get("1m", [])
    pos = "FLAT" if state.position is None else f"{state.position.shares:.4f} shares"
    print(f"[PaperTest] 1m closed | price={closed_1m.close:.4f} | candles={len(candles_1m)} | pos={pos}")

    # Flat → buy
    if state.position is None:
        print(f"[PaperTest] >>> BUY signal at {closed_1m.close:.4f}")
        return {"signal": "buy", "size": 1, "strategy_name": STRATEGY_NAME}

    # In position → sell
    print(f"[PaperTest] >>> SELL signal at {closed_1m.close:.4f}")
    return {"signal": "sell", "strategy_name": STRATEGY_NAME}
