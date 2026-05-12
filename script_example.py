# ta module: ta.sma, ta.ema, ta.vwap, ta.rsi, ta.atr, ta.bollinger, ta.macd
# Each candle: .time (datetime), .open, .high, .low, .close, .volume
# Extra numeric keys are auto-plotted as lines on the chart.
# Use {"value": x, "separate": True} to force a separate pane for one indicator.

def signals(candles):
    vwap = ta.vwap(candles)
    rsi14 = ta.rsi(candles, 14)

    results = []

    in_long = False
    rsi_armed = False

    for i, bar in enumerate(candles):
        signal = None
        markers = []

        if i > 0 and vwap[i] is not None and rsi14[i] is not None and rsi14[i - 1] is not None:
            close = bar.close

            price_below_vwap = close < vwap[i]

            # Arm setup when RSI goes below 30
            if not in_long and rsi14[i] < 30:
                rsi_armed = True

                markers.append({
                    "text": "RSI < 30",
                    "shape": "circle",
                    "position": "belowBar",
                    "color": "#f59e0b"
                })

            # Buy when:
            # 1. Price is below VWAP
            # 2. RSI was below 30
            # 3. RSI crosses back above 30
            if (
                not in_long
                and rsi_armed
                and price_below_vwap
                and rsi14[i - 1] < 30
                and rsi14[i] >= 30
            ):
                signal = "buy"
                in_long = True
                rsi_armed = False

                markers.append({
                    "text": "BUY RSI>30 below VWAP",
                    "shape": "arrowUp",
                    "position": "belowBar",
                    "color": "#22c55e"
                })

            # Sell only to exit long when price returns above VWAP
            elif (
                in_long
                and close > vwap[i]
            ):
                signal = "sell"
                in_long = False
                rsi_armed = False

                markers.append({
                    "text": "SELL VWAP+",
                    "shape": "arrowDown",
                    "position": "aboveBar",
                    "color": "#ef4444"
                })

        results.append({
            "time": bar.time.isoformat(),
            "signal": signal,

            # Main chart
            "vwap": vwap[i],

            # Separate pane
            "rsi_14": {
                "value": rsi14[i],
                "separate": True
            },
            "in_long": {
                "value": 1 if in_long else 0,
                "separate": True
            },
            "rsi_armed": {
                "value": 1 if rsi_armed else 0,
                "separate": True
            },

            "markers": markers,
        })

    return results