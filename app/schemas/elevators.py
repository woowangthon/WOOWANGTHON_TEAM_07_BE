from pydantic import BaseModel

from app.schemas.stations import LineRef


class StationRef(BaseModel):
    station_id: int
    station_name: str


class StationStatusSummary(BaseModel):
    status: str | None
    data_fresh: bool
    total_elevator_count: int
    unknown_elevator_count: int


class ElevatorWarningItem(BaseModel):
    """경로 위에서 주의가 필요한 역 한 곳. kind는 UNAVAILABLE(모두 가동 안됨), PARTIAL(일부 가동 안됨), UNCONFIRMED(확인 불가)."""

    station_id: int
    station_name: str
    kind: str
    message: str


class ElevatorWarning(BaseModel):
    """경로 위 역의 엘리베이터 경고. status: WARNING(가동 안됨·일부 가동 안됨 역 있음), UNCONFIRMED(확인 불가 역 있음), CLEAR(확인된 설비 기준 문제 없음)."""

    status: str
    items: list[ElevatorWarningItem]
    message: str


class ElevatorItem(BaseModel):
    """역별 엘리베이터 목록의 설비 한 건. docs/api-contract.md 4.4절."""

    elevator_id: int
    elevator_name: str
    location_description: str | None
    operation_section: str | None
    status: str | None
    data_fresh: bool
    unknown_reason: str | None
    observed_at: str | None
    fetched_at: str | None


class StationElevatorsResponse(BaseModel):
    """GET /api/stations/{station_id}/elevators 응답. docs/api-contract.md 4절."""

    station: StationRef
    station_status: StationStatusSummary
    limitations: list[str]
    elevators: list[ElevatorItem]


class ElevatorStatusItem(BaseModel):
    """전체 설비 상태 목록의 설비 한 건. docs/api-contract.md 5.3절."""

    elevator_id: int
    elevator_name: str
    station_id: int
    station_name: str
    lines: list[LineRef]  # 역이 지나는 전체 노선. line 필터와 무관하다
    status: str | None
    data_fresh: bool
    unknown_reason: str | None
    observed_at: str | None
    fetched_at: str | None


class ElevatorStatusListResponse(BaseModel):
    """GET /api/elevators/status 응답. docs/api-contract.md 5절."""

    elevators: list[ElevatorStatusItem]
    count: int
    limitations: list[str]


class StationStatusItem(BaseModel):
    """역 단위 상태 목록의 역 한 곳. docs/api-contract.md 5.5절."""

    station_id: int
    station_name: str
    lines: list[LineRef]  # 역이 지나는 전체 노선. line 필터와 무관하다
    station_status: StationStatusSummary


class StationStatusListResponse(BaseModel):
    """GET /api/stations/status 응답. docs/api-contract.md 5.5절."""

    stations: list[StationStatusItem]
    count: int
    limitations: list[str]
