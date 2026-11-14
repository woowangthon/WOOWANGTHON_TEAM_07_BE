from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_session
from app.schemas.elevators import ElevatorStatusListResponse, StationElevatorsResponse
from app.services.elevator_queries import get_station_elevators, list_elevator_statuses
from app.timeutil import utc_now

router = APIRouter(tags=["elevators"])

ElevatorStatusFilter = Literal["가동중", "가동 안됨"]


@router.get(
    "/api/stations/{station_id}/elevators",
    response_model=StationElevatorsResponse,
)
def read_station_elevators(
    station_id: int,
    session: Session = Depends(get_session),
) -> StationElevatorsResponse:
    """역별 엘리베이터 목록과 역 상태. 정수가 아니면 422, 0 이하이거나 없는 역은 404다."""
    return get_station_elevators(
        session,
        station_id,
        now=utc_now(),
        ttl_minutes=settings.elevator_status_ttl_minutes,
    )


@router.get("/api/elevators/status", response_model=ElevatorStatusListResponse)
def read_elevator_statuses(
    status: ElevatorStatusFilter | None = Query(
        None, description="설비 상태 필터. 가동중 또는 가동 안됨. 그 외 값은 422"
    ),
    session: Session = Depends(get_session),
) -> ElevatorStatusListResponse:
    """전체 설비 상태 목록. 확인된 설비만 status 필터에 포함된다."""
    return list_elevator_statuses(
        session,
        status=status,
        now=utc_now(),
        ttl_minutes=settings.elevator_status_ttl_minutes,
    )
