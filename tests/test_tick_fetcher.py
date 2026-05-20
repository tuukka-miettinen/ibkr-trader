from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.services.tick_fetcher import TickFetcher


class _DummyProvider:
    pass


def _fetcher() -> TickFetcher:
    return TickFetcher(provider=_DummyProvider())


def test_required_hours_uses_today_from_extended_session_start() -> None:
    fetcher = _fetcher()
    now = datetime(2026, 5, 20, 8, 0, 0, tzinfo=UTC)

    hours = fetcher._required_hours(
        start_date=date(2026, 5, 20),
        end_date=date(2026, 5, 20),
        extended=True,
        now=now,
    )

    assert len(hours) == 1
    assert hours[0] == datetime(2026, 5, 20, 8, 0, 0, tzinfo=UTC)


def test_same_day_range_before_rth_open_falls_back_to_previous_market_day() -> None:
    fetcher = _fetcher()
    now = datetime(2026, 5, 20, 8, 0, 0, tzinfo=UTC)

    resolved_start, resolved_end, trading_dates = fetcher.resolve_date_range(
        date(2026, 5, 20),
        date(2026, 5, 20),
        extended=False,
        now=now,
    )
    hours = fetcher._required_hours(
        start_date=date(2026, 5, 20),
        end_date=date(2026, 5, 20),
        extended=False,
        now=now,
    )

    assert resolved_start == date(2026, 5, 19)
    assert resolved_end == date(2026, 5, 19)
    assert trading_dates == [date(2026, 5, 19)]
    assert len(hours) == 7
    assert hours[0] == datetime(2026, 5, 19, 13, 0, 0, tzinfo=UTC)
    assert hours[-1] == datetime(2026, 5, 19, 19, 0, 0, tzinfo=UTC)


def test_required_hours_range_is_inclusive_and_starts_from_day_open() -> None:
    fetcher = _fetcher()
    now = datetime(2026, 5, 20, 15, 20, 0, tzinfo=UTC)

    hours = fetcher._required_hours(
        start_date=date(2026, 5, 18),
        end_date=date(2026, 5, 20),
        extended=True,
        now=now,
    )

    assert len(hours) == 40  # Mon full 16 + Tue full 16 + Wed partial 8 (08:00-15:00)
    assert hours[0] == datetime(2026, 5, 18, 8, 0, 0, tzinfo=UTC)
    assert hours[15] == datetime(2026, 5, 18, 23, 0, 0, tzinfo=UTC)
    assert hours[16] == datetime(2026, 5, 19, 8, 0, 0, tzinfo=UTC)
    assert hours[-1] == datetime(2026, 5, 20, 15, 0, 0, tzinfo=UTC)


def test_weekend_range_rolls_back_to_previous_weekday() -> None:
    fetcher = _fetcher()
    now = datetime(2026, 5, 20, 15, 20, 0, tzinfo=UTC)

    resolved_start, resolved_end, trading_dates = fetcher.resolve_date_range(
        date(2026, 5, 17),
        date(2026, 5, 17),
        extended=True,
        now=now,
    )

    assert resolved_start == date(2026, 5, 15)
    assert resolved_end == date(2026, 5, 15)
    assert trading_dates == [date(2026, 5, 15)]


def test_range_over_max_trading_days_is_rejected() -> None:
    fetcher = _fetcher()
    now = datetime(2026, 5, 20, 15, 20, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="maximum is 7"):
        fetcher.resolve_date_range(
            date(2026, 5, 8),
            date(2026, 5, 20),
            extended=True,
            now=now,
        )
