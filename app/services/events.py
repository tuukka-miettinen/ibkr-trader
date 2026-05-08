from __future__ import annotations

from datetime import timedelta

from app.models.market_data import TimelineEvent, TimelineEventType, utc_now


class EventService:
    def get_events(self, symbol: str) -> list[TimelineEvent]:
        normalized_symbol = symbol.upper()
        now = utc_now().replace(second=0, microsecond=0)
        return [
            TimelineEvent(
                id=f"{normalized_symbol}-earnings-last",
                symbol=normalized_symbol,
                event_type=TimelineEventType.EARNINGS,
                time=now - timedelta(days=20),
                title="Quarterly earnings released",
                summary="Beat on EPS with lighter guidance for next quarter.",
                details={
                    "eps_actual": 2.14,
                    "eps_estimate": 2.02,
                    "revenue_actual_b": 28.4,
                    "revenue_estimate_b": 27.9,
                },
            ),
            TimelineEvent(
                id=f"{normalized_symbol}-dividend-next",
                symbol=normalized_symbol,
                event_type=TimelineEventType.DIVIDEND,
                time=now + timedelta(days=14),
                title="Dividend ex-date",
                summary="Upcoming quarterly dividend event.",
                details={"cash_amount": 0.24},
            ),
            TimelineEvent(
                id=f"{normalized_symbol}-earnings-next",
                symbol=normalized_symbol,
                event_type=TimelineEventType.EARNINGS,
                time=now + timedelta(days=42),
                title="Next earnings expected",
                summary="Consensus expects moderate revenue growth.",
                details={"consensus_eps": 2.22},
            ),
        ]


event_service = EventService()
