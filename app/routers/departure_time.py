from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_session
from app.errors import CODE_VALIDATION_ERROR, AppError
from app.schemas.departure_time import DepartureTimeRequest, DepartureTimeResponse
from app.schemas.elevators import ElevatorWarning
from app.services.departure_time import parse_arrival_iso, recommend_departure, to_kst_dict
from app.services.route_service import require_station
from app.services.route_warning import build_elevator_warning, judge_stations_by_id, station_names
from app.services.routing import find_route
from app.services.status_messages import (
    DEP_ELEVATOR_UNCONFIRMED,
    ROUTE_SAME_STATION,
    WARN_NO_ROUTE_STATIONS,
)
from app.timeutil import utc_now

router = APIRouter(tags=["departure-time"])

_SEGMENT_FIELDS = (
    ("walk_to_station_minutes", "walk_to_station_minutes"),
    ("station_entry_minutes", "station_entry_minutes"),
    ("exit_walk_minutes", "exit_walk_minutes"),
)

STATION_IDS_TOGETHER = "from_station_id와 to_station_id는 함께 주어야 합니다."


def _elevator_warning(session: Session, body: DepartureTimeRequest, now) -> ElevatorWarning:
    """역 id가 없으면 DB를 쓰지 않고 미확인으로 둔다. 둘 다 있으면 경로 위 모든 역을 판정한다."""
    if body.from_station_id is None and body.to_station_id is None:
        return ElevatorWarning(status="UNCONFIRMED", items=[], message=DEP_ELEVATOR_UNCONFIRMED)
    if body.from_station_id is None or body.to_station_id is None:
        raise AppError(422, CODE_VALIDATION_ERROR, STATION_IDS_TOGETHER)
    require_station(session, body.from_station_id)
    require_station(session, body.to_station_id)
    if body.from_station_id == body.to_station_id:
        raise AppError(422, CODE_VALIDATION_ERROR, ROUTE_SAME_STATION)

    path = find_route(
        session,
        body.from_station_id,
        body.to_station_id,
        segment_minutes=settings.route_segment_minutes,
        transfer_minutes=settings.route_transfer_minutes,
    )
    if path is None:
        return ElevatorWarning(status="UNCONFIRMED", items=[], message=WARN_NO_ROUTE_STATIONS)

    station_ids: list[int] = []
    for node in path.nodes:
        if not station_ids or station_ids[-1] != node.station_id:
            station_ids.append(node.station_id)
    judgments = judge_stations_by_id(
        session, station_ids, now=now, ttl_minutes=settings.elevator_status_ttl_minutes
    )
    return build_elevator_warning(judgments, station_ids, station_names(session, station_ids))


@router.post("/api/departure-time", response_model=DepartureTimeResponse)
def calculate_departure_time(
    body: DepartureTimeRequest, session: Session = Depends(get_session)
) -> DepartureTimeResponse:
    """권장 출발 시각을 계산한다. 계산은 services.departure_time의 순수 함수를 그대로 쓴다.

    결과를 저장하지 않는다. 실제 열차 시간표를 쓰지 않으므로 항상 estimated=true다.
    from_station_id와 to_station_id를 함께 주면 경로 위 역의 엘리베이터 경고만 DB로 계산한다.
    """
    segments: dict[str, int] = {}
    defaults_applied: list[str] = []
    if "buffer_minutes" not in body.model_fields_set:
        defaults_applied.append("buffer_minutes")
    for field, setting_name in _SEGMENT_FIELDS:
        value = getattr(body, field)
        if value is None:
            value = getattr(settings, setting_name)
            defaults_applied.append(field)
        segments[field] = value

    now = utc_now()
    try:
        rec = recommend_departure(
            parse_arrival_iso(body.arrival_at),
            travel_minutes=body.travel_minutes,
            buffer_minutes=body.buffer_minutes,
            now=now.replace(microsecond=0),  # 서비스는 초 단위 시각만 받는다
            **segments,
        )
        data = to_kst_dict(rec)  # KST 변환. 날짜 범위를 벗어나면 OverflowError
    except (ValueError, OverflowError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "시각이 표현 가능한 범위를 벗어났습니다."
        raise AppError(422, CODE_VALIDATION_ERROR, message) from None

    warning = _elevator_warning(session, body, now)
    limitations = [*data["limitations"]]
    if warning.status != "CLEAR":
        limitations.append(warning.message)  # CLEAR는 제한 사항이 아니다

    ride = next(s for s in data["timeline"] if s["kind"] == "RIDE")
    return DepartureTimeResponse(
        recommended_departure_at=data["recommended_departure_at"],
        estimated_boarding_at=ride["start_at"],
        estimated_alighting_at=ride["end_at"],
        arrival_at=data["arrival_at"],
        total_minutes=data["total_minutes"],
        date_changed=data["date_changed"],
        already_past=data["already_past"],
        estimated=data["estimated"],
        timetable_used=data["timetable_used"],
        inputs=data["inputs"],
        defaults_applied=defaults_applied,
        timeline=data["timeline"],
        elevator_warning=warning,
        limitations=limitations,
    )
