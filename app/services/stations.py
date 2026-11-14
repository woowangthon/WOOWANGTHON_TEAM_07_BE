"""역 검색·상세 서비스. docs/api-contract.md 2절, 3절 계약을 따른다."""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import CODE_NOT_FOUND, AppError
from app.models import Line, Station, StationLine
from app.schemas.stations import LineRef, StationDetail, StationSearchResponse, StationSummary
from app.services.status_messages import LINE_1_PARTIAL

# docs/api-contract.md 0.4절 표준 문구 (D11). 문구를 바꾸면 계약 문서와 함께 바꾼다.
LINE_1_LIMITATION = LINE_1_PARTIAL  # docs/api-contract.md 0.4절 표준 문구(D11)
LINE_1 = "1"
NOT_FOUND_MESSAGE = "요청한 리소스를 찾을 수 없습니다."


def _load_lines(session: Session, station_ids: list[int]) -> dict[int, list[LineRef]]:
    """역별 전체 노선을 호선 오름차순으로 돌려준다. 노선 필터와 무관하게 전체 노선이다."""
    lines_by_station: dict[int, list[LineRef]] = defaultdict(list)
    if not station_ids:
        return lines_by_station
    rows = session.execute(
        select(StationLine.station_id, Line.line_id, Line.line_name)
        .join(Line, Line.line_id == StationLine.line_id)
        .where(StationLine.station_id.in_(station_ids))
    )
    for station_id, line_id, line_name in rows:
        lines_by_station[station_id].append(LineRef(line_id=line_id, line_name=line_name))
    for refs in lines_by_station.values():
        refs.sort(key=lambda ln: int(ln.line_id))
    return lines_by_station


def _includes_line_1(lines: list[LineRef]) -> bool:
    return any(ln.line_id == LINE_1 for ln in lines)


def search_stations(session: Session, keyword: str, line: str | None) -> StationSearchResponse:
    """역명에 keyword가 포함된 역을 찾는다.

    - keyword는 호출 전에 앞뒤 공백이 제거되어 있어야 한다.
    - line을 주면 그 노선을 지나는 역만 고르고, 응답의 lines에는 역의 전체 노선을 담는다.
    - 환승역은 station 한 행이므로 한 번만 나온다(역명·주소로 병합하지 않음).
    - 정렬: 역명 오름차순, 같은 역명이면 station_id 오름차순.
    """
    stmt = select(Station).where(Station.station_name.contains(keyword, autoescape=True))
    if line is not None:
        stmt = stmt.where(
            Station.station_id.in_(select(StationLine.station_id).where(StationLine.line_id == line))
        )
    stmt = stmt.order_by(Station.station_name, Station.station_id)
    stations = list(session.scalars(stmt))

    lines_by_station = _load_lines(session, [s.station_id for s in stations])
    summaries = [
        StationSummary(
            station_id=s.station_id,
            station_name=s.station_name,
            lines=lines_by_station[s.station_id],
        )
        for s in stations
    ]

    limitations: list[str] = []
    if line == LINE_1 or any(_includes_line_1(s.lines) for s in summaries):
        limitations.append(LINE_1_LIMITATION)

    return StationSearchResponse(stations=summaries, count=len(summaries), limitations=limitations)


def get_station_detail(session: Session, station_id: int) -> StationDetail:
    """역 한 개의 상세를 돌려준다. 0 이하이거나 없는 ID는 404 NOT_FOUND다."""
    if station_id < 1:
        raise AppError(404, CODE_NOT_FOUND, NOT_FOUND_MESSAGE)
    station = session.get(Station, station_id)
    if station is None:
        raise AppError(404, CODE_NOT_FOUND, NOT_FOUND_MESSAGE)

    lines = _load_lines(session, [station_id])[station_id]
    limitations = [LINE_1_LIMITATION] if _includes_line_1(lines) else []
    return StationDetail(
        station_id=station.station_id,
        station_name=station.station_name,
        latitude=station.latitude,
        longitude=station.longitude,
        is_transfer=len(lines) >= 2,
        lines=lines,
        limitations=limitations,
    )
