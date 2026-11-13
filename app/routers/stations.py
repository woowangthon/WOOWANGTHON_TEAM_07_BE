from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_session
from app.errors import CODE_VALIDATION_ERROR, AppError
from app.schemas.stations import StationDetail, StationSearchResponse
from app.services.stations import get_station_detail, search_stations

router = APIRouter(prefix="/api/stations", tags=["stations"])

KEYWORD_MIN = 1
KEYWORD_MAX = 20
LineId = Literal["1", "2", "3", "4", "5", "6", "7", "8"]


@router.get("", response_model=StationSearchResponse)
def list_stations(
    q: str = Query(..., description="역명 검색어. 앞뒤 공백을 제거한 뒤 1~20자"),
    line: LineId | None = Query(None, description="노선 필터 (1~8)"),
    session: Session = Depends(get_session),
) -> StationSearchResponse:
    keyword = q.strip()
    if not KEYWORD_MIN <= len(keyword) <= KEYWORD_MAX:
        raise AppError(
            422,
            CODE_VALIDATION_ERROR,
            f"검색어는 앞뒤 공백을 제외하고 {KEYWORD_MIN}~{KEYWORD_MAX}자여야 합니다.",
        )
    return search_stations(session, keyword, line)


@router.get("/{station_id}", response_model=StationDetail)
def read_station(
    station_id: int,
    session: Session = Depends(get_session),
) -> StationDetail:
    """역 상세. 정수가 아니면 422, 0 이하이거나 없는 ID는 404다."""
    return get_station_detail(session, station_id)
