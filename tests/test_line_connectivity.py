"""1~8호선 연결성과 끊긴 구간 점검(#31). 시드 파일을 임시 SQLite에만 적재하고 data/dev.db는 열지 않는다.

한계: 시간표(OA-101)에서 추출한 인접 간선(derive_edges)은 시간표를 수집한 DB가 있어야 만들 수 있어 여기서는 쓰지 않는다.
시드의 PROVISIONAL 인접 간선과 공식 TRANSFER 간선만 본다. 시드 기준으로는 어느 노선도 끊김 없이 이어져 있지 않다(미검증 구간).
"""

import csv
from dataclasses import dataclass

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Station, StationEdge, StationExternalId, StationLine
from app.services.edge_connectivity import connectivity_report
from app.services.edge_seed import load_edges, read_provisional_edges, read_verified_edges
from app.services.routing import find_route, load_graph
from app.services.station_seed import DEFAULT_SEED_DIR, load_seed, read_seed_dir

TRANSFER = "TRANSFER"
LINES = set("12345678")


@dataclass(frozen=True)
class LineStation:
    code: str
    station_line_id: int
    name: str


@pytest.fixture
def loaded(engine):
    with Session(engine) as session:
        rows, links, review = read_seed_dir(DEFAULT_SEED_DIR)
        load_seed(session, rows, links, review)
        load_edges(session, read_verified_edges(DEFAULT_SEED_DIR))
        load_edges(session, read_provisional_edges(DEFAULT_SEED_DIR), allow_provisional=True)
        yield session


def _stations_by_line(session) -> dict[str, list[LineStation]]:
    rows = session.execute(
        select(StationExternalId.external_station_code, StationExternalId.station_line_id, StationLine.line_id, Station.station_name)
        .join(StationLine, StationLine.station_line_id == StationExternalId.station_line_id)
        .join(Station, Station.station_id == StationLine.station_id)
        .where(StationExternalId.source == "OA-12035")
    ).all()
    by_line: dict[str, list[LineStation]] = {}
    for code, sl_id, line_id, name in rows:
        by_line.setdefault(line_id, []).append(LineStation(code, sl_id, name))
    return {line: sorted(items, key=lambda s: s.code) for line, items in by_line.items()}


def _adjacent_pairs(session) -> set[frozenset[int]]:
    return {
        frozenset(pair)
        for pair in session.execute(
            select(StationEdge.from_station_line_id, StationEdge.to_station_line_id).where(StationEdge.edge_type == "ADJACENT")
        )
    }


def _missing_segments(session) -> dict[str, list[tuple[LineStation, LineStation]]]:
    """노선별로 역번호 순서상 이웃한 두 역 사이에 인접 간선이 없는 구간."""
    adjacent = _adjacent_pairs(session)
    missing: dict[str, list[tuple[LineStation, LineStation]]] = {}
    for line, stations in _stations_by_line(session).items():
        missing[line] = [
            (a, b) for a, b in zip(stations, stations[1:])
            if frozenset((a.station_line_id, b.station_line_id)) not in adjacent
        ]
    return missing


def _describe(line: str, segments) -> str:
    return f"{line}호선 끊긴 구간 {len(segments)}곳: " + ", ".join(f"{a.name}({a.code})→{b.name}({b.code})" for a, b in segments)


def test_all_eight_lines_are_reported(loaded):
    assert set(connectivity_report(loaded)) == LINES
    assert set(_stations_by_line(loaded)) == LINES


def test_every_break_is_localized_to_named_segments(loaded):
    """컴포넌트가 둘 이상인 노선은 반드시 이름으로 특정되는 끊긴 구간이 있어야 한다. 숨은 끊김이 없다는 뜻이다."""
    report = connectivity_report(loaded)
    missing = _missing_segments(loaded)

    for line, info in report.items():
        segments = missing[line]
        assert len(info.component_sizes) <= len(segments) + 1, (
            f"{line}호선 컴포넌트 {info.component_sizes}가 끊긴 구간으로 설명되지 않는다. {_describe(line, segments)}"
        )
        if not info.connected:
            assert segments, f"{line}호선이 끊겨 있는데 끊긴 구간을 특정하지 못했다."


def test_code_neighbor_breaks_are_exactly_the_ones_listed_in_the_review_file(loaded):
    """역번호가 연속인데 간선이 없는 구간은 모두 검토 목록(BACKEND_CODE_NEIGHBOR_WITHOUT_EDGE)에 있고, 목록에 있는 구간은 실제로 끊겨 있다."""
    listed = {
        (r["line"], r["ref"])
        for r in csv.DictReader((DEFAULT_SEED_DIR / "station_edges_review.csv").open(encoding="utf-8"))
        if r["review_type"] == "BACKEND_CODE_NEIGHBOR_WITHOUT_EDGE"
    }
    actual = {
        (line, f"{a.code}-{b.code}")
        for line, segments in _missing_segments(loaded).items()
        for a, b in segments
        if int(b.code) - int(a.code) == 1
    }

    assert actual - listed == set(), f"검토 목록에 없는 끊긴 구간: {sorted(actual - listed)}"
    assert listed - actual == set(), f"목록에는 있으나 실제로는 이어진 구간: {sorted(listed - actual)}"


def test_transfer_edges_are_bidirectional_in_the_route_graph(loaded):
    graph, nodes = load_graph(loaded, segment_minutes=2, transfer_minutes=5)

    transfers = [(a, n) for a, neighbors in graph.items() for n in neighbors if n.edge_type == TRANSFER]
    assert len(transfers) == 2 * 38  # 환승 간선 38쌍이 양방향으로 들어간다
    for a, n in transfers:
        assert nodes[a].station_id == nodes[n.node].station_id and nodes[a].line_id != nodes[n.node].line_id
        assert any(back.node == a and back.edge_type == TRANSFER and back.minutes == n.minutes for back in graph[n.node])


def test_every_confirmed_transfer_group_is_one_component_of_distinct_lines(loaded):
    _, _, review = read_seed_dir(DEFAULT_SEED_DIR)
    graph, nodes = load_graph(loaded, segment_minutes=2, transfer_minutes=5)
    code_to_sl = {
        code: sl_id
        for code, sl_id in loaded.execute(
            select(StationExternalId.external_station_code, StationExternalId.station_line_id).where(StationExternalId.source == "OA-12035")
        )
    }

    for group in review.confirmed_groups:
        ids = {code_to_sl[c] for c in group.codes}
        assert len({nodes[i].line_id for i in ids}) == len(group.codes), f"{group.codes}: 서로 다른 노선이어야 한다"
        reachable = {next(iter(ids))}
        frontier = list(reachable)
        while frontier:  # 환승 간선만 따라가서 그룹 전체에 닿는지 본다
            current = frontier.pop()
            for n in graph[current]:
                if n.edge_type == TRANSFER and n.node not in reachable:
                    reachable.add(n.node)
                    frontier.append(n.node)
        assert reachable == ids, f"{group.codes}: 환승 간선만으로 그룹 전체에 닿지 않는다"


def test_route_across_a_transfer_station_exists_in_both_directions(loaded):
    """역삼(2호선)→교대 환승→남부터미널(3호선)과 그 반대가 같은 시간·환승 1회로 나온다."""
    ids = {name: loaded.scalar(select(Station.station_id).where(Station.station_name == name)) for name in ("역삼", "남부터미널(예술의전당)")}
    assert None not in ids.values()

    forward = find_route(loaded, ids["역삼"], ids["남부터미널(예술의전당)"], segment_minutes=2, transfer_minutes=5)
    backward = find_route(loaded, ids["남부터미널(예술의전당)"], ids["역삼"], segment_minutes=2, transfer_minutes=5)

    assert forward is not None and backward is not None
    assert forward.total_minutes == backward.total_minutes
    assert [h.edge_type for h in forward.hops].count(TRANSFER) == 1
    assert [h.edge_type for h in backward.hops].count(TRANSFER) == 1


def test_removing_an_adjacent_edge_is_reported_as_a_named_break(loaded):
    """끊김 탐지가 실제로 동작하는지: 강남–역삼 간선을 지우면 그 구간이 이름으로 보고되고 2호선 컴포넌트가 하나 늘어난다."""
    stations = {s.name: s for s in _stations_by_line(loaded)["2"]}
    gangnam, yeoksam = stations["강남"], stations["역삼"]
    segment = tuple(sorted((gangnam, yeoksam), key=lambda s: s.code))  # 구간은 역번호 순서로 보고된다
    before_segments = _missing_segments(loaded)["2"]
    before_components = len(connectivity_report(loaded)["2"].component_sizes)
    assert segment not in before_segments

    loaded.execute(delete(StationEdge).where(
        StationEdge.edge_type == "ADJACENT",
        StationEdge.from_station_line_id.in_((gangnam.station_line_id, yeoksam.station_line_id)),
        StationEdge.to_station_line_id.in_((gangnam.station_line_id, yeoksam.station_line_id)),
    ))
    loaded.commit()

    after_segments = _missing_segments(loaded)["2"]
    assert [pair for pair in after_segments if pair not in before_segments] == [segment]
    assert "강남(" in _describe("2", after_segments) and "역삼(" in _describe("2", after_segments)
    assert len(connectivity_report(loaded)["2"].component_sizes) == before_components + 1
