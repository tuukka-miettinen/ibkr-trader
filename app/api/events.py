from fastapi import APIRouter, Query

from app.models.market_data import TimelineEvent
from app.services.events import event_service

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=list[TimelineEvent])
def get_events(symbol: str = Query(default="NBIS", min_length=1, max_length=10)) -> list[TimelineEvent]:
    return event_service.get_events(symbol)
