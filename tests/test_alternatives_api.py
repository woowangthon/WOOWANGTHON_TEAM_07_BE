"""GET /api/stations/{id}/alternatives 검증. 그래프 픽스처는 임시 DB에만 넣는다(data/dev.db는 열지 않는다).

그래프(역 ID, 간선은 ADJACENT, 괄호는 노선):
  3강남(2) - 5교대(2) - 6서울대입구(2)
  5교대(3) - 7종로3가(3) - 2시청(1) - 1서울역(1)
  3강남(2) - 9신도림(2),  3강남(2) - 10정렬시험(2)
거리(3 기준): 5=1, 9=1, 10=1, 6=2, 7=2, 2=3, 1=4
"""

from datetime import timedelta

import pytest
from sqlalchemy import event, select

from app import config
from app.models import Elevator, ElevatorStatus, StationEdge, StationLine
from app.services.alternatives import bfs_distances
from app.services.status_messages import (
    ALT_NO_CANDIDATE_REASON,
    ALT_NO_EDGES,
    ALT_TARGET_OK_REASON,
    ALT_UNVERIFIED,
    LINE_1_PARTIAL,
)
from app.timeutil import to_utc_iso, utc_now

RUNNING, STOPPED = "가동중", "가동 안됨"


def _add_elevator(db, eid, station_id, status=None, age=timedelta(0)):
    db.add(Elevator(elevator_id=eid, station_id=station_id, source="OA-15994",
                    external_station_code=f"ST{station_id}", elevator_name=f"설비{eid}",
                    location_description=None, operation_section=None))
    db.flush()
    if status:
        db.add(ElevatorStatus(elevator_id=eid, status=status, source_status_text=None, observed_at=None,
                              fetched_at=to_utc_iso(utc_now() - age), source="OA-15994"))
        db.flush()


def _edge(db, a, a_line, b, b_line, edge_type="ADJACENT"):
    def sl(station, line):
        return db.scalar(select(StationLine.station_line_id).where(
            StationLine.station_id == station, StationLine.line_id == line))
    db.add(StationEdge(from_station_line_id=sl(a, a_line), to_station_line_id=sl(b, b_line),
                       edge_type=edge_type, source=None))
    db.flush()


@pytest.fixture
def graph(db):
    for a, al, b, bl in [(3, "2", 5, "2"), (5, "2", 6, "2"), (5, "3", 7, "3"), (7, "1", 2, "1"),
                         (2, "1", 1, "1"), (3, "2", 9, "2"), (3, "2", 10, "2")]:
        _edge(db, a, al, b, bl)
    _add_elevator(db, 301, 3, STOPPED)                 # 대상: 전부 고장
    _add_elevator(db, 501, 5, RUNNING)
    _add_elevator(db, 502, 5, STOPPED)                 # 교대: PARTIAL
    _add_elevator(db, 601, 6, RUNNING)                 # AVAILABLE
    _add_elevator(db, 701, 7, RUNNING)                 # AVAILABLE, 다른 노선
    _add_elevator(db, 201, 2, RUNNING)                 # 거리 3
    _add_elevator(db, 901, 9, STOPPED)                 # UNAVAILABLE -> 제외
    # 10(정렬시험)은 설비 없음 -> 제외
    db.commit()
    return db


def ids(body):
    return [a["station_id"] for a in body["alternatives"]]


def test_candidates_are_sorted_same_line_then_distance(client, graph):
    body = client.get("/api/stations/3/alternatives").json()

    # 5(같은 노선, 1) -> 6(같은 노선, 2) -> 7(다른 노선, 2). 거리 3인 2는 기본 깊이 2 밖이다.
    assert ids(body) == [5, 6, 7]
    assert [a["distance"] for a in body["alternatives"]] == [1, 2, 2]
    assert [a["same_line"] for a in body["alternatives"]] == [True, True, False]
    assert body["count"] == 3 and body["search_depth"] == 2
    assert body["empty_reason"] is None
    assert body["station_status"]["status"] == "UNAVAILABLE"
    assert body["alternatives"][0]["station_status"]["status"] == "PARTIAL"
    assert body["alternatives"][1]["station_status"]["status"] == "AVAILABLE"


def test_unavailable_and_no_elevator_stations_are_excluded(client, graph):
    body = client.get("/api/stations/3/alternatives").json()

    assert 9 not in ids(body) and 10 not in ids(body)


def test_limitations_always_state_unverified_access(client, graph):
    body = client.get("/api/stations/3/alternatives").json()

    assert ALT_UNVERIFIED in body["limitations"]
    assert LINE_1_PARTIAL in body["limitations"]  # 후보 7이 1호선을 지난다
    assert "검증되지 않음" in ALT_UNVERIFIED


def test_depth_comes_from_settings(client, graph, monkeypatch):
    monkeypatch.setattr(config.settings, "alternative_search_depth", 3)
    body = client.get("/api/stations/3/alternatives").json()

    assert ids(body) == [5, 6, 2, 7]  # 2는 거리 3, 같은 노선(2호선 환승역)이라 7보다 앞이다
    monkeypatch.setattr(config.settings, "alternative_search_depth", 1)
    assert ids(client.get("/api/stations/3/alternatives").json()) == [5]


def test_healthy_target_returns_empty_with_reason(client, graph):
    body = client.get("/api/stations/6/alternatives").json()

    assert body["station_status"] == {"status": "AVAILABLE", "data_fresh": True,
                                      "total_elevator_count": 1, "unknown_elevator_count": 0}
    assert body["alternatives"] == [] and body["count"] == 0
    assert body["empty_reason"] == ALT_TARGET_OK_REASON
    assert ALT_UNVERIFIED in body["limitations"]


def test_stale_available_target_still_searches(client, db, graph):
    _add_elevator(db, 801, 8, RUNNING)
    _add_elevator(db, 802, 8, RUNNING, age=timedelta(hours=2))  # 일부 미확인 -> data_fresh False
    _edge(db, 8, "1", 7, "1")
    db.commit()

    body = client.get("/api/stations/8/alternatives").json()

    assert body["station_status"]["status"] == "AVAILABLE" and body["station_status"]["data_fresh"] is False
    assert ids(body) == [7, 2, 5]  # 7(1호선, 1) -> 2(1호선, 2) -> 5(다른 노선, 2)


def test_target_without_edges_reports_no_edges(client, graph):
    body = client.get("/api/stations/4/alternatives").json()  # 간선 없음

    assert body["alternatives"] == []
    assert body["empty_reason"] == ALT_NO_EDGES
    assert ALT_NO_EDGES in body["limitations"]


def test_empty_graph_never_invents_candidates(client, db):
    _add_elevator(db, 301, 3, STOPPED)
    _add_elevator(db, 601, 6, RUNNING)
    db.commit()

    body = client.get("/api/stations/3/alternatives").json()

    assert body["alternatives"] == [] and body["empty_reason"] == ALT_NO_EDGES


def test_transfer_edges_do_not_create_station_links(client, db):
    _edge(db, 1, "1", 1, "4", edge_type="TRANSFER")
    _add_elevator(db, 101, 1, STOPPED)
    db.commit()

    body = client.get("/api/stations/1/alternatives").json()

    assert body["alternatives"] == [] and body["empty_reason"] == ALT_NO_EDGES


def test_no_candidate_in_range_reason(client, db):
    _edge(db, 3, "2", 9, "2")
    _add_elevator(db, 301, 3, STOPPED)
    _add_elevator(db, 901, 9, STOPPED)
    db.commit()

    body = client.get("/api/stations/3/alternatives").json()

    assert body["alternatives"] == [] and body["empty_reason"] == ALT_NO_CANDIDATE_REASON


@pytest.mark.parametrize("station_id", [99, 0, -1])
def test_missing_station_is_404(client, graph, station_id):
    response = client.get(f"/api/stations/{station_id}/alternatives")

    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"


def test_non_integer_station_is_422(client, graph):
    response = client.get("/api/stations/abc/alternatives")

    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_response_fields(client, graph):
    body = client.get("/api/stations/3/alternatives").json()

    assert set(body) == {"station", "station_status", "alternatives", "count", "search_depth",
                         "empty_reason", "limitations"}
    assert set(body["alternatives"][0]) == {"station_id", "station_name", "lines", "distance",
                                            "same_line", "station_status"}


def _count_selects(db, client, url):
    statements = []

    def before(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", before)
    try:
        db.expunge_all()
        client.get(url)
    finally:
        event.remove(db.bind, "before_cursor_execute", before)
    return len(statements)


def test_query_count_is_constant(client, graph, monkeypatch):
    few = _count_selects(graph, client, "/api/stations/3/alternatives")
    monkeypatch.setattr(config.settings, "alternative_search_depth", 4)
    many = _count_selects(graph, client, "/api/stations/3/alternatives")

    assert few == many <= 6


def test_bfs_depth_cycles_and_undirected_edges():
    adjacency = {1: {2}, 2: {1, 3}, 3: {2, 1}, 4: set()}

    assert bfs_distances(adjacency, 1, 1) == {1: 0, 2: 1}
    assert bfs_distances(adjacency, 1, 5) == {1: 0, 2: 1, 3: 2}
    assert bfs_distances(adjacency, 9, 3) == {9: 0}
