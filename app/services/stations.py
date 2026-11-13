"""역 검색 서비스. docs/api-contract.md 2절 계약을 따른다."""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Line, Station, StationLine
from app.schemas.stations import LineRef, StationSearchResponse, StationSummary

# docs/api-contract.md 0.4절 표준 문구 (D11). 문구를 바꾸면 계약 문서와 함께 바꾼다.
LINE_1_LIMITATION = "1호선은 일부 구간의 역만 포함되어 있어 전체 구간을 포함하지 않음"
LINE_1 = "1"


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

    lines_by_station: dict[int, list[LineRef]] = defaultdict(list)
    if stations:
        rows = session.execute(
            select(StationLine.station_id, Line.line_id, Line.line_name)
            .join(Line, Line.line_id == StationLine.line_id)
            .where(StationLine.station_id.in_([s.station_id for s in stations]))
        )
        for station_id, line_id, line_name in rows:
            lines_by_station[station_id].append(LineRef(line_id=line_id, line_name=line_name))

    summaries = [
        StationSummary(
            station_id=s.station_id,
            station_name=s.station_name,
            lines=sorted(lines_by_station[s.station_id], key=lambda ln: int(ln.line_id)),
        )
        for s in stations
    ]

    limitations: list[str] = []
    includes_line_1 = line == LINE_1 or any(ln.line_id == LINE_1 for s in summaries for ln in s.lines)
    if includes_line_1:
        limitations.append(LINE_1_LIMITATION)

    return StationSearchResponse(stations=summaries, count=len(summaries), limitations=limitations)
