from app.models.market_data import Timeframe
from app.services.backtest import DEFAULT_LOOKBACK_DAYS, resolve_lookback_limit


def test_resolve_lookback_limit_maps_days_to_trading_day_bars() -> None:
    # resolve_lookback_limit adds +1 day of bars so the provider fetches enough;
    # the excess is trimmed in run_backtest_core after fetching.
    assert resolve_lookback_limit(Timeframe.ONE_MINUTE, days=1) == 390 * 2
    assert resolve_lookback_limit(Timeframe.THREE_MINUTES, days=1) == 130 * 2
    assert resolve_lookback_limit(Timeframe.FIVE_MINUTES, days=1) == 78 * 2
    assert resolve_lookback_limit(Timeframe.FIFTEEN_MINUTES, days=1) == 26 * 2
    assert resolve_lookback_limit(Timeframe.ONE_HOUR, days=1) == 7 * 2


def test_resolve_lookback_limit_defaults_to_trading_month_for_legacy_calls() -> None:
    assert resolve_lookback_limit(Timeframe.FIVE_MINUTES) == 78 * DEFAULT_LOOKBACK_DAYS