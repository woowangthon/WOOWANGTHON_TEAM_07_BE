from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_session
from app.schemas.routes import RouteResponse
from app.services.route_service import get_route
from app.timeutil import utc_now

router = APIRouter(tags=["routes"])


@router.get("/api/routes", response_model=RouteResponse)
def read_route(
    from_station_id: int = Query(..., alias="from", description="출발역 station_id"),
    to_station_id: int = Query(..., alias="to", description="도착역 station_id"),
    session: Session = Depends(get_session),
) -> RouteResponse:
    """두 역 사이의 최단 경로(추정). 정수가 아니거나 두 역이 같으면 422, 없는 역이나 경로가 없으면 404다."""
    return get_route(
        session,
        from_station_id,
        to_station_id,
        now=utc_now(),
        ttl_minutes=settings.elevator_status_ttl_minutes,
        segment_minutes=settings.route_segment_minutes,
        transfer_minutes=settings.route_transfer_minutes,
    )
