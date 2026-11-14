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
    q: str | None = Query(
        None,
        description=(
            "역명 검색어. 생략하면 전체 역 목록(line 필터는 적용)을 돌려준다. "
            "주면 앞뒤 공백을 제거한 뒤 1~20자여야 하며, 빈 값·공백만 있는 값은 422다."
        ),
    ),
    line: LineId | None = Query(None, description="노선 필터 (1~8)"),
    session: Session = Depends(get_session),
) -> StationSearchResponse:
    """역 목록·검색 (docs/api-contract.md 2절).

    q를 생략하면 전체 역을 돌려준다. 명시한 빈 검색어(`q=`, `q=%20`)는 생략과 구분해 422로 거절한다.
    """
    if q is None:
        return search_stations(session, None, line)
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
