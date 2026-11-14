from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_session
from app.errors import CODE_NOT_FOUND, AppError
from app.models import Station
from app.schemas.arrivals import ArrivalItem, ArrivalsResponse
from app.services.arrivals import ArrivalService, get_arrival_service
from app.timeutil import to_kst_iso, utc_now

router = APIRouter(tags=["arrivals"])

NOT_FOUND_MESSAGE = "요청한 리소스를 찾을 수 없습니다."


@router.get("/api/stations/{station_id}/arrivals", response_model=ArrivalsResponse)
def read_arrivals(
    station_id: int,
    session: Session = Depends(get_session),
    service: ArrivalService = Depends(get_arrival_service),
) -> ArrivalsResponse:
    """실시간 도착 정보. 정수가 아니면 422, 0 이하이거나 없는 역은 404다. 확인하지 못하면 200과 UNCONFIRMED다."""
    station = session.get(Station, station_id) if station_id >= 1 else None
    if station is None:
        raise AppError(404, CODE_NOT_FOUND, NOT_FOUND_MESSAGE)

    result = service.lookup(station.station_id, station.station_name, utc_now().replace(microsecond=0))
    items = [
        ArrivalItem(
            line=a.line,
            direction=a.direction,
            destination=a.destination,
            eta_seconds=a.eta_seconds,
            estimated_arrival_at=to_kst_iso(result.fetched_at + timedelta(seconds=a.eta_seconds)),
        )
        for a in result.arrivals
    ]
    return ArrivalsResponse(
        station={"station_id": station.station_id, "station_name": station.station_name},
        arrival_status=result.status,
        unconfirmed_reason=result.unconfirmed_reason,
        arrivals=items,
        count=len(items),
        fetched_at=to_kst_iso(result.fetched_at) if result.fetched_at else None,
        cache_hit=result.cache_hit,
        cache_ttl_seconds=service.ttl_seconds,
        limitations=list(result.limitations),
    )
