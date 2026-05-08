"""Execute a user-supplied strategy script in an isolated namespace.

The script must define:
    signals(candles: list) -> list[dict]

Each dict must have at minimum:
    {"time": str (ISO), "signal": "buy" | "sell" | None}

Any extra keys in each dict (e.g. "sma_20", "rsi") are treated as indicator
series and are returned in the API response for the frontend to plot.
To control placement explicitly, return indicators as
{"value": number | None, "separate": bool}.

The `ta` module and Candle class are injected automatically so scripts can call
ta.sma(), ta.ema(), ta.vwap(), ta.rsi(), ta.atr(), ta.bollinger(), ta.macd().
"""
from __future__ import annotations

from app.models.market_data import Candle
from app.strategy import indicators as ta


def validate_user_script(script: str) -> None:
    namespace: dict = {"ta": ta, "Candle": Candle}
    try:
        exec(compile(script, "<strategy>", "exec"), namespace)  # noqa: S102
    except SyntaxError as exc:
        raise ValueError(f"Syntax error in script: {exc}") from exc

    fn = namespace.get("signals")
    if not callable(fn):
        raise ValueError("Script must define a function named 'signals(candles)'")


def run_user_script(script: str, candles: list[Candle]) -> list[dict]:
    validate_user_script(script)

    namespace: dict = {"ta": ta, "Candle": Candle}
    exec(compile(script, "<strategy>", "exec"), namespace)  # noqa: S102

    fn = namespace["signals"]

    try:
        result = fn(candles)
    except Exception as exc:
        raise ValueError(f"Error running signals(): {exc}") from exc

    if not isinstance(result, list):
        raise ValueError("signals() must return a list")

    normalised = []
    for i, item in enumerate(result):
        if not isinstance(item, dict) or "time" not in item or "signal" not in item:
            raise ValueError(
                f"signals() item {i} must be a dict with 'time' and 'signal' keys"
            )
        normalised.append({"time": item["time"], "signal": item.get("signal"), **{
            k: v for k, v in item.items() if k not in ("time", "signal")
        }})
    return normalised
