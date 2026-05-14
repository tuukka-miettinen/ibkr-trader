STRATEGY_NAME = "Strict Volatility VWAP Rebound Scalper"

def on_tick(state):
    # =========================
    # Config
    # =========================
    RSI_LENGTH = 14

    MAX_ENTRIES = 4
    ENTRY_SIZE = 1

    # Dip entry rules
    RSI_BUY_LEVEL = 34
    RSI_PANIC_LEVEL = 26
    RSI_BOUNCE_LEVEL = 41

    # Must be meaningfully below VWAP
    VWAP_DISCOUNT_REQUIRED = 0.004    # 0.40% below VWAP

    # Add only if price moves clearly lower
    MIN_ADD_DROP = 0.02               # add every 2% lower

    # Big dip memory
    BIG_DIP_FROM_LAST_BUY = 0.03
    BIG_DIP_MEMORY_BARS = 10
    BIG_DIP_RSI_MAX = 42

    # Panic dip
    PANIC_DROP_1M = 0.025             # 2.5% 1m close-to-close drop

    # =========================
    # Stricter volatility filter
    # =========================
    VOL_LOOKBACK_BARS = 15

    # Need actual movement, not only RSI weakness
    MIN_RECENT_RANGE = 0.012          # 1.2% high-low range over lookback
    MIN_AVG_CANDLE_RANGE = 0.0025     # average 1m candle range 0.25%
    MIN_DROP_FROM_RECENT_HIGH = 0.010 # price must be at least 1.0% below recent high

    # For VWAP dump double entry
    MIN_VWAP_DUMP_RANGE = 0.015       # 1.5% range over lookback

    # Fee-aware exits
    TAKE_PROFIT = 0.009               # +0.9%

    # Trailing exit
    TRAIL_ACTIVATE = 0.007            # activate after +0.7%
    TRAIL_FROM_TOP = 0.0045           # sell if falls 0.45% from high
    MIN_PROFIT_TO_SELL = 0.0035       # minimum +0.35%

    # Momentum stall exit
    STALL_PROFIT = 0.006
    STALL_RSI_LEVEL = 62

    # VWAP dump double-entry logic
    VWAP_DROP_1M_DOUBLE = 0.005       # VWAP drops 0.5% in 1 candle
    VWAP_DROP_3M_DOUBLE = 0.009       # or 0.9% over 3 candles
    VWAP_OVERSOLD_RSI = 28
    VWAP_DOUBLE_ENTRY_SIZE = 2
    VWAP_REBOUND_MAX_BARS = 25
    VWAP_POSITIVE_PROFIT = 0.0025     # sell at VWAP only if +0.25% green

    # Anti-fee churn
    BUY_COOLDOWN_BARS = 12
    MIN_HOLD_BARS_BEFORE_SELL = 3

    # =========================
    # Load state
    # =========================
    entries = state.strategy.get("entries", 0)
    highest_price = state.strategy.get("highest_price", None)
    last_entry_price = state.strategy.get("last_entry_price", None)
    first_entry_bar_count = state.strategy.get("first_entry_bar_count", None)

    last_buy_bar_count = state.strategy.get("last_buy_bar_count", None)
    last_sell_bar_count = state.strategy.get("last_sell_bar_count", None)
    big_dip_bar_count = state.strategy.get("big_dip_bar_count", None)

    vwap_rebound_mode = state.strategy.get("vwap_rebound_mode", False)
    vwap_rebound_bar_count = state.strategy.get("vwap_rebound_bar_count", None)
    vwap_double_entry_done = state.strategy.get("vwap_double_entry_done", False)

    # =========================
    # Need closed 1m candle
    # =========================
    closed_1m = state.closed.get("1m")
    candles_1m = state.candles.get("1m", [])

    if closed_1m is None or len(candles_1m) < RSI_LENGTH + VOL_LOOKBACK_BARS:
        return {
            "signal": None,
            "strategy_name": STRATEGY_NAME
        }

    bar_count = len(candles_1m)

    # =========================
    # Reset when flat
    # =========================
    if state.position is None:
        state.strategy["entries"] = 0
        state.strategy["highest_price"] = None
        state.strategy["last_entry_price"] = None
        state.strategy["first_entry_bar_count"] = None
        state.strategy["big_dip_bar_count"] = None
        state.strategy["vwap_rebound_mode"] = False
        state.strategy["vwap_rebound_bar_count"] = None
        state.strategy["vwap_double_entry_done"] = False

        entries = 0
        highest_price = None
        last_entry_price = None
        first_entry_bar_count = None
        big_dip_bar_count = None
        vwap_rebound_mode = False
        vwap_rebound_bar_count = None
        vwap_double_entry_done = False

    # =========================
    # Indicators
    # =========================
    rsi_1m = ta.rsi(candles_1m, RSI_LENGTH)
    vwap_1m = ta.vwap(candles_1m)

    current_rsi = rsi_1m[-1]
    prev_rsi = rsi_1m[-2]

    current_vwap = vwap_1m[-1]
    prev_vwap = vwap_1m[-2]
    vwap_3m_ago = vwap_1m[-4]

    if (
        current_rsi is None or
        prev_rsi is None or
        current_vwap is None or
        prev_vwap is None or
        vwap_3m_ago is None
    ):
        return {
            "signal": None,
            "strategy_name": STRATEGY_NAME
        }

    bar = candles_1m[-1]
    prev_bar = candles_1m[-2]

    price = bar.close
    one_min_drop = (bar.close - prev_bar.close) / prev_bar.close

    # =========================
    # Strict volatility filter
    # =========================
    recent_candles = candles_1m[-VOL_LOOKBACK_BARS:]

    recent_high = max(c.high for c in recent_candles)
    recent_low = min(c.low for c in recent_candles)

    recent_range = (recent_high - recent_low) / price
    drop_from_recent_high = (recent_high - price) / recent_high

    avg_candle_range = sum(
        (c.high - c.low) / c.close
        for c in recent_candles
        if c.close > 0
    ) / len(recent_candles)

    last_candle_range = (bar.high - bar.low) / price

    # Normal buy needs sustained movement, not just one small red candle.
    volatility_ok = (
        recent_range >= MIN_RECENT_RANGE and
        drop_from_recent_high >= MIN_DROP_FROM_RECENT_HIGH and
        avg_candle_range >= MIN_AVG_CANDLE_RANGE
    )

    # Panic exception: allow if current candle is genuinely large.
    panic_volatility_ok = (
        recent_range >= MIN_RECENT_RANGE and
        last_candle_range >= 0.006 and
        one_min_drop <= -PANIC_DROP_1M
    )

    buy_volatility_ok = volatility_ok or panic_volatility_ok

    vwap_double_volatility_ok = (
        recent_range >= MIN_VWAP_DUMP_RANGE and
        drop_from_recent_high >= MIN_DROP_FROM_RECENT_HIGH
    )

    # =========================
    # VWAP dump detection
    # =========================
    vwap_drop_1m = (current_vwap - prev_vwap) / prev_vwap
    vwap_drop_3m = (current_vwap - vwap_3m_ago) / vwap_3m_ago

    vwap_dump = (
        vwap_drop_1m <= -VWAP_DROP_1M_DOUBLE or
        vwap_drop_3m <= -VWAP_DROP_3M_DOUBLE
    )

    vwap_rebound_active = (
        vwap_rebound_mode and
        vwap_rebound_bar_count is not None and
        bar_count - vwap_rebound_bar_count <= VWAP_REBOUND_MAX_BARS
    )

    if vwap_rebound_mode and not vwap_rebound_active:
        state.strategy["vwap_rebound_mode"] = False
        state.strategy["vwap_rebound_bar_count"] = None
        state.strategy["vwap_double_entry_done"] = False

        vwap_rebound_mode = False
        vwap_rebound_bar_count = None
        vwap_double_entry_done = False

    # =========================
    # Big dip memory
    # =========================
    big_dip_from_last_buy = (
        last_entry_price is not None and
        price <= last_entry_price * (1 - BIG_DIP_FROM_LAST_BUY)
    )

    if big_dip_from_last_buy:
        state.strategy["big_dip_bar_count"] = bar_count
        big_dip_bar_count = bar_count

    big_dip_recent = (
        big_dip_bar_count is not None and
        bar_count - big_dip_bar_count <= BIG_DIP_MEMORY_BARS
    )

    # =========================
    # Minimum hold
    # =========================
    if first_entry_bar_count is None:
        min_hold_ok = True
    else:
        min_hold_ok = bar_count - first_entry_bar_count >= MIN_HOLD_BARS_BEFORE_SELL

    # =========================
    # Sell logic
    # =========================
    if state.position is not None:
        avg_price = state.position.avg_price

        if highest_price is None or bar.high > highest_price:
            highest_price = bar.high
            state.strategy["highest_price"] = highest_price

        current_profit = (price - avg_price) / avg_price
        highest_profit = (highest_price - avg_price) / avg_price if highest_price is not None else 0

        # VWAP rebound sell after double-entry
        if (
            vwap_rebound_active and
            min_hold_ok and
            price >= current_vwap and
            current_profit >= VWAP_POSITIVE_PROFIT
        ):
            state.strategy["entries"] = 0
            state.strategy["highest_price"] = None
            state.strategy["last_entry_price"] = None
            state.strategy["first_entry_bar_count"] = None
            state.strategy["big_dip_bar_count"] = None
            state.strategy["vwap_rebound_mode"] = False
            state.strategy["vwap_rebound_bar_count"] = None
            state.strategy["vwap_double_entry_done"] = False
            state.strategy["last_sell_bar_count"] = bar_count

            return {
                "signal": "sell",
                "strategy_name": STRATEGY_NAME
            }

        # Main profit exit
        if min_hold_ok and current_profit >= TAKE_PROFIT:
            state.strategy["entries"] = 0
            state.strategy["highest_price"] = None
            state.strategy["last_entry_price"] = None
            state.strategy["first_entry_bar_count"] = None
            state.strategy["big_dip_bar_count"] = None
            state.strategy["vwap_rebound_mode"] = False
            state.strategy["vwap_rebound_bar_count"] = None
            state.strategy["vwap_double_entry_done"] = False
            state.strategy["last_sell_bar_count"] = bar_count

            return {
                "signal": "sell",
                "strategy_name": STRATEGY_NAME
            }

        # Trailing profit exit
        trailing_active = highest_profit >= TRAIL_ACTIVATE
        trailing_stop = highest_price * (1 - TRAIL_FROM_TOP) if highest_price is not None else None

        if (
            min_hold_ok and
            trailing_active and
            trailing_stop is not None and
            current_profit >= MIN_PROFIT_TO_SELL and
            price <= trailing_stop
        ):
            state.strategy["entries"] = 0
            state.strategy["highest_price"] = None
            state.strategy["last_entry_price"] = None
            state.strategy["first_entry_bar_count"] = None
            state.strategy["big_dip_bar_count"] = None
            state.strategy["vwap_rebound_mode"] = False
            state.strategy["vwap_rebound_bar_count"] = None
            state.strategy["vwap_double_entry_done"] = False
            state.strategy["last_sell_bar_count"] = bar_count

            return {
                "signal": "sell",
                "strategy_name": STRATEGY_NAME
            }

        # Momentum stall exit
        if (
            min_hold_ok and
            current_profit >= STALL_PROFIT and
            current_rsi >= STALL_RSI_LEVEL and
            bar.close < bar.open
        ):
            state.strategy["entries"] = 0
            state.strategy["highest_price"] = None
            state.strategy["last_entry_price"] = None
            state.strategy["first_entry_bar_count"] = None
            state.strategy["big_dip_bar_count"] = None
            state.strategy["vwap_rebound_mode"] = False
            state.strategy["vwap_rebound_bar_count"] = None
            state.strategy["vwap_double_entry_done"] = False
            state.strategy["last_sell_bar_count"] = bar_count

            return {
                "signal": "sell",
                "strategy_name": STRATEGY_NAME
            }

    # =========================
    # Cooldowns
    # =========================
    buy_cooldown_ok = (
        last_buy_bar_count is None or
        bar_count - last_buy_bar_count >= BUY_COOLDOWN_BARS
    )

    sell_cooldown_ok = (
        last_sell_bar_count is None or
        bar_count - last_sell_bar_count >= BUY_COOLDOWN_BARS
    )

    # =========================
    # VWAP dump double-entry buy
    # =========================
    can_double_entry = (
        state.position is not None and
        vwap_double_volatility_ok and
        vwap_dump and
        current_rsi <= VWAP_OVERSOLD_RSI and
        price <= current_vwap and
        not vwap_double_entry_done and
        entries <= MAX_ENTRIES - VWAP_DOUBLE_ENTRY_SIZE and
        buy_cooldown_ok
    )

    if can_double_entry:
        state.strategy["entries"] = entries + VWAP_DOUBLE_ENTRY_SIZE
        state.strategy["last_entry_price"] = price
        state.strategy["last_buy_bar_count"] = bar_count
        state.strategy["vwap_rebound_mode"] = True
        state.strategy["vwap_rebound_bar_count"] = bar_count
        state.strategy["vwap_double_entry_done"] = True
        state.strategy["big_dip_bar_count"] = None

        if first_entry_bar_count is None:
            state.strategy["first_entry_bar_count"] = bar_count

        if highest_price is None:
            state.strategy["highest_price"] = bar.high

        return {
            "signal": "buy",
            "size": VWAP_DOUBLE_ENTRY_SIZE,
            "strategy_name": STRATEGY_NAME
        }

    # =========================
    # Normal buy logic
    # =========================
    below_vwap_enough = price <= current_vwap * (1 - VWAP_DISCOUNT_REQUIRED)

    rsi_cross_down = (
        prev_rsi > RSI_BUY_LEVEL and
        current_rsi <= RSI_BUY_LEVEL
    )

    oversold_bounce = (
        prev_rsi <= RSI_PANIC_LEVEL and
        current_rsi > prev_rsi and
        current_rsi <= RSI_BOUNCE_LEVEL and
        bar.close > bar.open
    )

    panic_drop = (
        one_min_drop <= -PANIC_DROP_1M and
        current_rsi <= RSI_PANIC_LEVEL
    )

    big_dip_rebuy_signal = (
        big_dip_recent and
        below_vwap_enough and
        current_rsi <= BIG_DIP_RSI_MAX
    )

    if (
        entries < MAX_ENTRIES and
        buy_volatility_ok and
        buy_cooldown_ok and
        sell_cooldown_ok and
        not vwap_rebound_active
    ):
        if entries == 0:
            price_spaced = True
        else:
            price_spaced = (
                last_entry_price is not None and
                price <= last_entry_price * (1 - MIN_ADD_DROP)
            )

        first_buy_signal = (
            below_vwap_enough and
            (
                rsi_cross_down or
                oversold_bounce or
                panic_drop
            )
        )

        add_buy_signal = (
            below_vwap_enough and
            (
                (
                    price_spaced and
                    (
                        current_rsi <= RSI_PANIC_LEVEL or
                        panic_drop
                    )
                )
                or
                big_dip_rebuy_signal
            )
        )

        if entries == 0:
            should_buy = first_buy_signal
        else:
            should_buy = add_buy_signal

        if should_buy:
            state.strategy["entries"] = entries + 1
            state.strategy["last_entry_price"] = price
            state.strategy["last_buy_bar_count"] = bar_count
            state.strategy["big_dip_bar_count"] = None

            if entries == 0:
                state.strategy["first_entry_bar_count"] = bar_count

            if highest_price is None:
                state.strategy["highest_price"] = bar.high

            return {
                "signal": "buy",
                "size": ENTRY_SIZE,
                "strategy_name": STRATEGY_NAME
            }

    return {
        "signal": None,
        "strategy_name": STRATEGY_NAME
    }