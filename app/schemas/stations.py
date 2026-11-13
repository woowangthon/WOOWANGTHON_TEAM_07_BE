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
