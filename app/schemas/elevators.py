from pydantic import BaseModel


class StationRef(BaseModel):
    station_id: int
    station_name: str


class StationStatusSummary(BaseModel):
    status: str | None
    data_fresh: bool
    total_elevator_count: int
    unknown_elevator_count: int


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
