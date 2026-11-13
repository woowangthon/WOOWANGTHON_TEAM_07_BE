from pydantic import BaseModel


class LineRef(BaseModel):
    line_id: str
    line_name: str


class StationSummary(BaseModel):
    station_id: int
    station_name: str
    lines: list[LineRef]


class StationSearchResponse(BaseModel):
    stations: list[StationSummary]
    count: int
    limitations: list[str]


class StationDetail(BaseModel):
    station_id: int
    station_name: str
    latitude: float | None
    longitude: float | None
    is_transfer: bool
    lines: list[LineRef]
    limitations: list[str]
