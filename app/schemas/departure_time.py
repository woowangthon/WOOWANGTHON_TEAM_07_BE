from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr

from app.departure_defaults import BUFFER_MINUTES_DEFAULT
from app.schemas.elevators import ElevatorWarning


class DepartureTimeRequest(BaseModel):
    """POST /api/departure-time 요청. docs/api-contract.md 8.2절.

    알 수 없는 필드는 거부하고, 분 값은 정수만 받는다(문자열·실수·bool 거부). 범위는 서비스가 검사한다.
    """

    model_config = ConfigDict(extra="forbid")

    arrival_at: StrictStr  # 오프셋이 있는 ISO 8601, 초 단위
    travel_minutes: StrictInt  # 외부에서 받은 예상 이동 시간
    buffer_minutes: StrictInt = BUFFER_MINUTES_DEFAULT  # 승강장 대기·여유 시간. 기본 5분(추정값)
    walk_to_station_minutes: StrictInt | None = None  # 생략하면 설정 기본값
    station_entry_minutes: StrictInt | None = None
    exit_walk_minutes: StrictInt | None = None
    # 둘 다 주면 경로 위 역의 엘리베이터 경고를 계산한다. 이동 시간(travel_minutes)은 여전히 외부 입력이다.
    from_station_id: StrictInt | None = None
    to_station_id: StrictInt | None = None


class DepartureInputsOut(BaseModel):
    travel_minutes: int
    buffer_minutes: int
    walk_to_station_minutes: int
    station_entry_minutes: int
    exit_walk_minutes: int


class TimelineStepOut(BaseModel):
    kind: str
    label: str
    start_at: str
    end_at: str
    minutes: int


class DepartureTimeResponse(BaseModel):
    recommended_departure_at: str
    estimated_boarding_at: str  # 시간표를 쓰지 않은 추정. 열차 탑승 확정 시각이 아니다
    estimated_alighting_at: str  # 위와 같음
    arrival_at: str  # 요청한 도착 희망 시각
    total_minutes: int
    date_changed: bool
    already_past: bool
    estimated: bool
    timetable_used: bool
    inputs: DepartureInputsOut
    defaults_applied: list[str]
    timeline: list[TimelineStepOut]
    elevator_warning: ElevatorWarning
    limitations: list[str]
