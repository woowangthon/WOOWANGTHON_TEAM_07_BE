"""경로 응답 조립. 이슈 #37 / docs/api-contract.md 8.4절.

경로 탐색(routing), 엘리베이터 판정(route_warning), 노선 이름 조회를 묶어 응답으로 만든다.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import CODE_NOT_FOUND, CODE_VALIDATION_ERROR, AppError
from app.models import Line, Station
from app.schemas.elevators import StationRef
from app.schemas.routes import RouteResponse, RouteStation, RouteStep
from app.services.elevator_queries import NOT_FOUND_MESSAGE
from app.services.route_warning import build_elevator_warning, judge_stations_by_id, summary_of
from app.services.routing import ADJACENT, RoutePath, find_route
from app.services.status_messages import (
    LINE_1_PARTIAL,
    ROUTE_ACCESSIBILITY_UNVERIFIED,
    ROUTE_EDGE_PROVISIONAL,
    ROUTE_EDGE_TIMETABLE,
    ROUTE_ESTIMATED,
    ROUTE_NOT_FOUND,
    ROUTE_SAME_STATION,
)
from app.services.stations import LINE_1, _load_lines

SOURCE_TIMETABLE = "OA-101"
SOURCE_PROVISIONAL = "OA-12035+FE-MOCK"


def require_station(session: Session, station_id: int) -> Station:
    """없거나 0 이하인 역은 404다(계약 3.4절과 같은 규칙)."""
    station = session.get(Station, station_id) if station_id >= 1 else None
    if station is None:
        raise AppError(404, CODE_NOT_FOUND, NOT_FOUND_MESSAGE)
    return station


def _steps(path: RoutePath, names: dict[int, str], line_names: dict[str, str]) -> list[RouteStep]:
    """연속한 ADJACENT 간선을 노선별 RIDE로 묶고, TRANSFER는 따로 둔다."""
    steps: list[RouteStep] = []
    for hop in path.hops:
        if hop.edge_type == ADJACENT:
            last = steps[-1] if steps else None
            if last is not None and last.kind == "RIDE" and last.line_id == hop.from_node.line_id:
                last.minutes += hop.minutes
                last.stops = (last.stops or 0) + 1
                last.to_station = StationRef(station_id=hop.to_node.station_id, station_name=names[hop.to_node.station_id])
                last.label = f"{last.line_name} {last.from_station.station_name} → {last.to_station.station_name}"
                continue
            line_name = line_names[hop.from_node.line_id]
            start = StationRef(station_id=hop.from_node.station_id, station_name=names[hop.from_node.station_id])
            end = StationRef(station_id=hop.to_node.station_id, station_name=names[hop.to_node.station_id])
            steps.append(
                RouteStep(
                    kind="RIDE", label=f"{line_name} {start.station_name} → {end.station_name}",
                    minutes=hop.minutes, line_id=hop.from_node.line_id, line_name=line_name,
                    from_station=start, to_station=end, stops=1,
                )
            )
        else:
            station = StationRef(station_id=hop.from_node.station_id, station_name=names[hop.from_node.station_id])
            steps.append(
                RouteStep(
                    kind="TRANSFER",
                    label=f"{station.station_name}에서 {line_names[hop.from_node.line_id]} → {line_names[hop.to_node.line_id]} 환승",
                    minutes=hop.minutes, station=station,
                    from_line_id=hop.from_node.line_id, to_line_id=hop.to_node.line_id,
                )
            )
    return steps


def get_route(
    session: Session,
    from_station_id: int,
    to_station_id: int,
    *,
    now: datetime,
    ttl_minutes: int,
    segment_minutes: int,
    transfer_minutes: int,
) -> RouteResponse:
    origin = require_station(session, from_station_id)
    destination = require_station(session, to_station_id)
    if from_station_id == to_station_id:
        raise AppError(422, CODE_VALIDATION_ERROR, ROUTE_SAME_STATION)

    path = find_route(
        session, from_station_id, to_station_id,
        segment_minutes=segment_minutes, transfer_minutes=transfer_minutes,
    )
    if path is None:
        raise AppError(404, CODE_NOT_FOUND, ROUTE_NOT_FOUND)

    station_ids: list[int] = []
    for node in path.nodes:
        if not station_ids or station_ids[-1] != node.station_id:
            station_ids.append(node.station_id)

    names = {s.station_id: s.station_name for s in session.scalars(select(Station).where(Station.station_id.in_(station_ids)))}
    line_names = dict(session.execute(select(Line.line_id, Line.line_name)).all())
    lines = _load_lines(session, station_ids)
    judgments = judge_stations_by_id(session, station_ids, now=now, ttl_minutes=ttl_minutes)
    transfer_stations = {hop.from_node.station_id for hop in path.hops if hop.edge_type != ADJACENT}

    sources = {hop.source for hop in path.hops if hop.edge_type == ADJACENT}
    limitations = [ROUTE_ESTIMATED]
    if SOURCE_TIMETABLE in sources:
        limitations.append(ROUTE_EDGE_TIMETABLE)
    if SOURCE_PROVISIONAL in sources:
        limitations.append(ROUTE_EDGE_PROVISIONAL)
    limitations.append(ROUTE_ACCESSIBILITY_UNVERIFIED)
    if any(ln.line_id == LINE_1 for sid in station_ids for ln in lines[sid]):
        limitations.append(LINE_1_PARTIAL)

    return RouteResponse(
        from_station=StationRef(station_id=origin.station_id, station_name=origin.station_name),
        to_station=StationRef(station_id=destination.station_id, station_name=destination.station_name),
        stations=[
            RouteStation(
                station_id=sid, station_name=names[sid], lines=lines[sid],
                is_transfer=sid in transfer_stations, station_status=summary_of(judgments[sid]),
            )
            for sid in station_ids
        ],
        transfer_count=len(transfer_stations),
        total_minutes=path.total_minutes,
        estimated=True,
        timeline=_steps(path, names, line_names),
        elevator_warning=build_elevator_warning(judgments, station_ids, names),
        limitations=limitations,
    )
