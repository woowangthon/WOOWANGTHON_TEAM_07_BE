from pydantic import BaseModel

from app.schemas.elevators import ElevatorWarning, StationRef, StationStatusSummary
from app.schemas.stations import LineRef


class RouteStation(BaseModel):
    station_id: int
    station_name: str
    lines: list[LineRef]  # 역이 지나는 전체 노선
    is_transfer: bool  # 경로에서 이 역에서 노선을 갈아탐
    station_status: StationStatusSummary


class RouteStep(BaseModel):
    """타임라인 한 구간. kind가 RIDE면 line_id~stops, TRANSFER면 from_line_id·to_line_id가 채워진다."""

    kind: str  # RIDE | TRANSFER
    label: str
    minutes: int
    line_id: str | None = None
    line_name: str | None = None
    from_station: StationRef | None = None
    to_station: StationRef | None = None
    stops: int | None = None  # RIDE: 지나는 역 간 구간 수
    station: StationRef | None = None  # TRANSFER: 환승역
    from_line_id: str | None = None
    to_line_id: str | None = None


class RouteResponse(BaseModel):
    """GET /api/routes 응답. docs/api-contract.md 8.4절."""

    from_station: StationRef
    to_station: StationRef
    stations: list[RouteStation]
    transfer_count: int
    total_minutes: int
    estimated: bool
    timeline: list[RouteStep]
    elevator_warning: ElevatorWarning
    limitations: list[str]
