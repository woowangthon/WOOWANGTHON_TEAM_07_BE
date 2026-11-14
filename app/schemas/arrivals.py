from typing import Literal

from pydantic import BaseModel

from app.schemas.elevators import StationRef


class ArrivalItem(BaseModel):
    line: str | None
    direction: str | None
    destination: str | None
    eta_seconds: int  # fetched_at 시점의 남은 시간
    estimated_arrival_at: str  # fetched_at + eta_seconds (KST)


class ArrivalsResponse(BaseModel):
    """GET /api/stations/{station_id}/arrivals 응답. docs/api-contract.md 8.3절(초안)."""

    station: StationRef
    arrival_status: Literal["CONFIRMED", "EMPTY", "UNCONFIRMED"]
    unconfirmed_reason: Literal["PROVIDER_NOT_CONFIGURED", "PROVIDER_ERROR"] | None
    arrivals: list[ArrivalItem]
    count: int
    fetched_at: str | None
    cache_hit: bool
    cache_ttl_seconds: int
    limitations: list[str]
