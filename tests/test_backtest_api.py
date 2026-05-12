from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


SCRIPT_WITH_TRADES = """\
def signals(candles):
    results = []
    for i, bar in enumerate(candles):
        signal = None
        if i % 10 == 1:
            signal = \"buy\"
        elif i % 10 == 5:
            signal = \"sell\"
        results.append({\"time\": bar.time.isoformat(), \"signal\": signal})
    return results
"""


def test_backtest_quick_returns_rows_and_daily_breakdown() -> None:
    response = client.post(
        "/api/backtest/quick",
        json={
            "symbol": "AAPL",
            "timeframes": ["1m", "3m", "5m", "15m"],
            "limit": 288,
            "starting_capital": 20000,
            "position_size": 1500,
            "max_entries": 3,
            "script": SCRIPT_WITH_TRADES,
        },
    )

    assert response.status_code == 200
    body = response.json()

    assert body["symbol"] == "AAPL"
    assert len(body["rows"]) == 4
    assert body["best_timeframe"] in ["1m", "3m", "5m", "15m"]

    for row in body["rows"]:
        assert row["timeframe"] in ["1m", "3m", "5m", "15m"]
        assert row["status"] == "ok"
        assert "summary" in row
        assert "daily" in row
        assert row["summary"]["starting_capital"] == 20000
        assert "final_balance" in row["summary"]
        assert "total_dollar_pnl" in row["summary"]


def test_backtest_quick_rejects_empty_timeframes() -> None:
    response = client.post(
        "/api/backtest/quick",
        json={
            "symbol": "AAPL",
            "timeframes": [],
            "limit": 288,
            "script": SCRIPT_WITH_TRADES,
        },
    )

    assert response.status_code == 422
    assert "at least one timeframe is required" in response.text


def test_backtest_detail_supports_three_minute_timeframe() -> None:
    response = client.post(
        "/api/backtest",
        json={
            "symbol": "AAPL",
            "timeframe": "3m",
            "limit": 288,
            "starting_capital": 12000,
            "position_size": 750,
            "max_entries": 4,
            "script": SCRIPT_WITH_TRADES,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["timeframe"] == "3m"
    assert "summary" in body
    assert body["summary"]["starting_capital"] == 12000
    assert "final_balance" in body["summary"]
    if body["trades"]:
        trade = body["trades"][0]
        assert "shares" in trade
        assert "total_cost" in trade
        assert "entries" in trade


def test_backtest_detail_resolves_days_to_timeframe_bar_count() -> None:
    response = client.post(
        "/api/backtest",
        json={
            "symbol": "AAPL",
            "timeframe": "15m",
            "days": 1,
            "script": SCRIPT_WITH_TRADES,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["timeframe"] == "15m"
    # days=1 should trim candles to exactly 1 unique trading date
    dates = {c["time"].split("T", 1)[0] for c in body["candles"]}
    assert len(dates) == 1
