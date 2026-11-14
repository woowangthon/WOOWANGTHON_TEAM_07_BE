from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_session
from app.schemas.alternatives import AlternativesResponse
from app.services.alternatives import get_alternatives
from app.timeutil import utc_now

router = APIRouter(tags=["alternatives"])


@router.get("/api/stations/{station_id}/alternatives", response_model=AlternativesResponse)
def read_alternatives(
    station_id: int,
    session: Session = Depends(get_session),
) -> AlternativesResponse:
    """대체역 후보. 정수가 아니면 422, 0 이하이거나 없는 역은 404다."""
    return get_alternatives(
        session,
        station_id,
        now=utc_now(),
        ttl_minutes=settings.elevator_status_ttl_minutes,
        max_depth=settings.alternative_search_depth,
    )
