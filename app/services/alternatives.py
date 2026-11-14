"""대체역 조회. 이슈 #33 / docs/api-contract.md 8.1절(초안).

- 후보는 station_edges의 ADJACENT 간선을 BFS로 탐색해 찾는다. 간선이 없으면 후보도 없다.
  데이터에 없는 환승·인접 관계는 만들지 않는다. TRANSFER 간선은 같은 역 안의 노선 연결이라
  역 그래프에는 쓰지 않는다.
- 상태 판정은 status_judgment의 순수 함수를 그대로 쓴다.
- 후보는 가동중 설비가 확인된 역(AVAILABLE 또는 PARTIAL)만 담는다. UNAVAILABLE, 정보 없음 역은 제외한다.
- 정렬: 대상 역과 노선을 공유하는 역 먼저, 거리, AVAILABLE 먼저, 신선한 데이터 먼저, station_id.
- 조회: 역, 간선, 후보 역, 설비+상태, 노선 각 1회(후보 수와 무관).
"""

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.errors import CODE_NOT_FOUND, AppError
from app.models import Station, StationEdge, StationLine
from app.schemas.alternatives import AlternativeStation, AlternativesResponse
from app.schemas.elevators import StationRef, StationStatusSummary
from app.services.elevator_queries import NOT_FOUND_MESSAGE, _elevator_rows, _judge
from app.services.status_judgment import (
    STATION_AVAILABLE,
    STATION_PARTIAL,
    StationJudgment,
    judge_station_without_elevators,
)
from app.services.status_messages import (
    ALT_NO_CANDIDATE_REASON,
    ALT_NO_EDGES,
    ALT_TARGET_OK_REASON,
    ALT_UNVERIFIED,
    LINE_1_PARTIAL,
)
from app.services.stations import LINE_1, _load_lines

_STATUS_RANK = {STATION_AVAILABLE: 0, STATION_PARTIAL: 1}


def bfs_distances(adjacency: dict[int, set[int]], start: int, max_depth: int) -> dict[int, int]:
    """start에서 max_depth 간선 이내의 역과 최단 거리(간선 수). start는 0이다. 간선은 양방향이다."""
    distances = {start: 0}
    frontier = [start]
    for depth in range(1, max_depth + 1):
        next_frontier: list[int] = []
        for node in frontier:
            for neighbor in sorted(adjacency.get(node, ())):
                if neighbor not in distances:
                    distances[neighbor] = depth
                    next_frontier.append(neighbor)
        frontier = next_frontier
        if not frontier:
            break
    return distances


def _load_adjacency(session: Session) -> dict[int, set[int]]:
    src = aliased(StationLine)
    dst = aliased(StationLine)
    rows = session.execute(
        select(src.station_id, dst.station_id)
        .select_from(StationEdge)
        .join(src, src.station_line_id == StationEdge.from_station_line_id)
        .join(dst, dst.station_line_id == StationEdge.to_station_line_id)
        .where(StationEdge.edge_type == "ADJACENT")
    )
    adjacency: dict[int, set[int]] = defaultdict(set)
    for a, b in rows:
        if a != b:
            adjacency[a].add(b)
            adjacency[b].add(a)
    return adjacency


def _summary(j: StationJudgment) -> StationStatusSummary:
    return StationStatusSummary(
        status=j.status,
        data_fresh=j.data_fresh,
        total_elevator_count=j.total_elevator_count,
        unknown_elevator_count=j.unknown_elevator_count,
    )


def get_alternatives(
    session: Session, station_id: int, *, now: datetime, ttl_minutes: int, max_depth: int
) -> AlternativesResponse:
    if station_id < 1:
        raise AppError(404, CODE_NOT_FOUND, NOT_FOUND_MESSAGE)
    target = session.get(Station, station_id)
    if target is None:
        raise AppError(404, CODE_NOT_FOUND, NOT_FOUND_MESSAGE)

    adjacency = _load_adjacency(session)
    distances = bfs_distances(adjacency, station_id, max_depth)
    ids = sorted(distances)

    stations = {s.station_id: s for s in session.scalars(select(Station).where(Station.station_id.in_(ids)))}
    rows = _elevator_rows(session, station_ids=ids)
    judged = {j.station_id: j for j in _judge(rows, now=now, ttl_minutes=ttl_minutes)} if rows else {}
    lines = _load_lines(session, ids)

    def judgment(sid: int) -> StationJudgment:
        return judged.get(sid) or judge_station_without_elevators(sid)

    target_judgment = judgment(station_id)
    target_line_ids = {ln.line_id for ln in lines[station_id]}

    alternatives: list[AlternativeStation] = []
    reason: str | None = None
    if target_judgment.status == STATION_AVAILABLE and target_judgment.data_fresh:
        reason = ALT_TARGET_OK_REASON
    else:
        for sid in ids:
            j = judgment(sid)
            if sid == station_id or j.status not in _STATUS_RANK:
                continue
            alternatives.append(
                AlternativeStation(
                    station_id=sid,
                    station_name=stations[sid].station_name,
                    lines=lines[sid],
                    distance=distances[sid],
                    same_line=bool(target_line_ids & {ln.line_id for ln in lines[sid]}),
                    station_status=_summary(j),
                )
            )
        alternatives.sort(
            key=lambda a: (
                not a.same_line,
                a.distance,
                _STATUS_RANK[a.station_status.status],
                not a.station_status.data_fresh,
                a.station_id,
            )
        )
        if not alternatives:
            reason = ALT_NO_CANDIDATE_REASON if station_id in adjacency else ALT_NO_EDGES

    limitations = [ALT_UNVERIFIED]
    if station_id not in adjacency:
        limitations.append(ALT_NO_EDGES)
    shown = [lines[station_id]] + [a.lines for a in alternatives]
    if any(ln.line_id == LINE_1 for group in shown for ln in group):
        limitations.append(LINE_1_PARTIAL)

    return AlternativesResponse(
        station=StationRef(station_id=target.station_id, station_name=target.station_name),
        station_status=_summary(target_judgment),
        alternatives=alternatives,
        count=len(alternatives),
        search_depth=max_depth,
        empty_reason=reason,
        limitations=limitations,
    )
