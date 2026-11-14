"""경로 탐색(다익스트라). 이슈 #36, #37 / docs/api-contract.md 8.4절.

- 그래프의 노드는 역-노선(station_line_id)이다. 간선은 station_edges의 ADJACENT(역 간)와 TRANSFER(환승)다.
  간선은 양방향으로 쓴다.
- 가중치는 설정의 추정값이다. 역 간 ROUTE_SEGMENT_MINUTES(기본 2분), 환승 ROUTE_TRANSFER_MINUTES(기본 5분).
  검증된 소요 시간이 아니므로 응답은 항상 estimated=true다.
- 출발역과 도착역은 역 단위다. 출발역의 모든 노선 노드에서 시작하고 도착역의 어느 노선 노드에 닿아도 된다.
- 같은 비용이면 노드 id가 작은 쪽을 먼저 확정해 결과가 항상 같다.
- 외부 API를 호출하지 않는다. 결과를 저장하지 않는다.
- 엘리베이터 상태는 경로 선택에 쓰지 않는다. 경로 위 역의 상태는 경고로만 붙인다(route_warning).
"""

import heapq
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import StationEdge, StationLine

ADJACENT = "ADJACENT"
TRANSFER = "TRANSFER"


@dataclass(frozen=True)
class Neighbor:
    node: int
    minutes: int
    edge_type: str
    source: str | None


Graph = dict[int, list[Neighbor]]


@dataclass(frozen=True)
class PathNode:
    station_line_id: int
    station_id: int
    line_id: str


@dataclass(frozen=True)
class PathHop:
    from_node: PathNode
    to_node: PathNode
    minutes: int
    edge_type: str
    source: str | None


@dataclass(frozen=True)
class RoutePath:
    nodes: list[PathNode]
    hops: list[PathHop]
    total_minutes: int


def shortest_path(
    graph: Graph, sources: list[int], targets: set[int]
) -> tuple[int, list[int]] | None:
    """여러 시작 노드에서 목표 노드 집합 중 하나까지의 최단 경로. (총 분, 노드 id 목록) 또는 None.

    시작 노드와 목표 노드가 겹치면 비용 0의 한 노드 경로다.
    """
    best: dict[int, int] = {}
    previous: dict[int, int | None] = {}
    heap: list[tuple[int, int]] = []
    for node in sorted(set(sources)):
        best[node] = 0
        previous[node] = None
        heapq.heappush(heap, (0, node))

    while heap:
        cost, node = heapq.heappop(heap)
        if cost > best.get(node, cost):
            continue
        if node in targets:
            path = [node]
            while previous[path[-1]] is not None:
                path.append(previous[path[-1]])  # type: ignore[arg-type]
            path.reverse()
            return cost, path
        for neighbor in graph.get(node, ()):
            new_cost = cost + neighbor.minutes
            if new_cost < best.get(neighbor.node, new_cost + 1):
                best[neighbor.node] = new_cost
                previous[neighbor.node] = node
                heapq.heappush(heap, (new_cost, neighbor.node))
    return None


def load_graph(
    session: Session, *, segment_minutes: int, transfer_minutes: int
) -> tuple[Graph, dict[int, PathNode]]:
    """station_edges와 station_lines를 읽어 그래프를 만든다. 쿼리는 2번이다."""
    nodes = {
        sl_id: PathNode(sl_id, station_id, line_id)
        for sl_id, station_id, line_id in session.execute(
            select(StationLine.station_line_id, StationLine.station_id, StationLine.line_id)
        )
    }
    graph: Graph = defaultdict(list)
    rows = session.execute(
        select(
            StationEdge.from_station_line_id,
            StationEdge.to_station_line_id,
            StationEdge.edge_type,
            StationEdge.source,
        ).order_by(StationEdge.from_station_line_id, StationEdge.to_station_line_id)
    )
    for a, b, edge_type, source in rows:
        if a not in nodes or b not in nodes:
            continue
        minutes = transfer_minutes if edge_type == TRANSFER else segment_minutes
        graph[a].append(Neighbor(b, minutes, edge_type, source))
        graph[b].append(Neighbor(a, minutes, edge_type, source))
    return graph, nodes


def find_route(
    session: Session,
    from_station_id: int,
    to_station_id: int,
    *,
    segment_minutes: int,
    transfer_minutes: int,
) -> RoutePath | None:
    """두 역 사이의 최단 경로. 없으면 None."""
    graph, nodes = load_graph(session, segment_minutes=segment_minutes, transfer_minutes=transfer_minutes)
    sources = [n.station_line_id for n in nodes.values() if n.station_id == from_station_id]
    targets = {n.station_line_id for n in nodes.values() if n.station_id == to_station_id}
    found = shortest_path(graph, sources, targets)
    if found is None:
        return None
    total, ids = found
    hops: list[PathHop] = []
    for a, b in zip(ids, ids[1:]):
        neighbor = next(n for n in graph[a] if n.node == b)
        hops.append(PathHop(nodes[a], nodes[b], neighbor.minutes, neighbor.edge_type, neighbor.source))
    return RoutePath(nodes=[nodes[i] for i in ids], hops=hops, total_minutes=total)
