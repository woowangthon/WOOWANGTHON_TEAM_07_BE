from fastapi import APIRouter

from app.config import settings
from app.errors import CODE_VALIDATION_ERROR, AppError
from app.schemas.departure_time import DepartureTimeRequest, DepartureTimeResponse
from app.services.departure_time import parse_arrival_iso, recommend_departure, to_kst_dict
from app.services.status_messages import DEP_ELEVATOR_UNCONFIRMED
from app.timeutil import utc_now

router = APIRouter(tags=["departure-time"])

_SEGMENT_FIELDS = (
    ("walk_to_station_minutes", "walk_to_station_minutes"),
    ("station_entry_minutes", "station_entry_minutes"),
    ("exit_walk_minutes", "exit_walk_minutes"),
)


@router.post("/api/departure-time", response_model=DepartureTimeResponse)
def calculate_departure_time(body: DepartureTimeRequest) -> DepartureTimeResponse:
    """권장 출발 시각을 계산한다. 계산은 services.departure_time의 순수 함수를 그대로 쓴다.

    DB·외부 API를 쓰지 않고 결과를 저장하지 않는다. 실제 열차 시간표를 쓰지 않으므로 항상 estimated=true다.
    """
    segments: dict[str, int] = {}
    defaults_applied: list[str] = []
    for field, setting_name in _SEGMENT_FIELDS:
        value = getattr(body, field)
        if value is None:
            value = getattr(settings, setting_name)
            defaults_applied.append(field)
        segments[field] = value

    try:
        rec = recommend_departure(
            parse_arrival_iso(body.arrival_at),
            travel_minutes=body.travel_minutes,
            buffer_minutes=body.buffer_minutes,
            now=utc_now().replace(microsecond=0),  # 서비스는 초 단위 시각만 받는다
            **segments,
        )
        data = to_kst_dict(rec)  # KST 변환. 날짜 범위를 벗어나면 OverflowError
    except (ValueError, OverflowError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "시각이 표현 가능한 범위를 벗어났습니다."
        raise AppError(422, CODE_VALIDATION_ERROR, message) from None

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
        # 역·설비 상태를 입력받거나 조회하지 않으므로 경고를 만들지 않고 미확인으로 표시한다.
        elevator_warning={"status": "UNCONFIRMED", "items": [], "message": DEP_ELEVATOR_UNCONFIRMED},
        limitations=[*data["limitations"], DEP_ELEVATOR_UNCONFIRMED],
    )
