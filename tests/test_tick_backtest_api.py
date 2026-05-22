from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_tick_backtest_stream_returns_error_event_when_fetch_fails() -> None:
    payload = {
        "symbol": "AAPL",
        "start_date": "2026-05-11",
        "end_date": "2026-05-12",
        "script": "def on_tick(state):\n    return {\"signal\": None}\n",
    }

    with patch(
        "app.api.tick_backtest.tick_fetcher.resolve_date_range",
        return_value=(date(2026, 5, 11), date(2026, 5, 12), [date(2026, 5, 11), date(2026, 5, 12)]),
    ):
        with patch(
            "app.api.tick_backtest.tick_fetcher.fetch_and_store",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            with client.stream("POST", "/api/tick-backtest/run", json=payload) as response:
                assert response.status_code == 200
                lines = list(response.iter_lines())

    assert any('"stage": "fetch"' in line for line in lines)
    assert any('"stage": "error"' in line and 'boom' in line for line in lines)
