from pydantic import BaseModel

from app.schemas.elevators import StationRef, StationStatusSummary
from app.schemas.stations import LineRef


class AlternativeStation(BaseModel):
    station_id: int
    station_name: str
    lines: list[LineRef]
    distance: int  # 대상 역에서 간선 수 기준 거리
    same_line: bool  # 대상 역과 노선을 하나라도 공유하는지(경로의 노선을 뜻하지 않는다)
    station_status: StationStatusSummary


class AlternativesResponse(BaseModel):
    """GET /api/stations/{station_id}/alternatives 응답. docs/api-contract.md 8.1절(초안)."""

    station: StationRef
    station_status: StationStatusSummary
    alternatives: list[AlternativeStation]
    count: int
    search_depth: int
    empty_reason: str | None
    limitations: list[str]
