"""경로 탐색(다익스트라)과 GET /api/routes 검증. 그래프는 임시 DB에만 넣는다.

그래프(역 ID, 괄호는 노선, ADJACENT는 -, TRANSFER는 =):
  3강남(2) - 5교대(2) - 6서울대입구(2)
  5교대(2) = 5교대(3) - 7종로3가(3) = 7종로3가(1) - 2시청(1) - 1서울역(1)
  9신도림(2)와 10정렬시험(2)는 연결이 없다(고립).
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import config
from app.models import Elevator, ElevatorStatus, StationEdge, StationLine
from app.services.routing import Neighbor, shortest_path
from app.services.status_messages import (
    LINE_1_PARTIAL,
    ROUTE_ACCESSIBILITY_UNVERIFIED,
    ROUTE_EDGE_PROVISIONAL,
    ROUTE_EDGE_TIMETABLE,
    ROUTE_ESTIMATED,
    ROUTE_NOT_FOUND,
    ROUTE_SAME_STATION,
    WARN_CLEAR,
    WARN_UNCONFIRMED,
    WARN_WARNING,
)
from app.timeutil import to_utc_iso, utc_now

RUNNING, STOPPED = "가동중", "가동 안됨"


def _sl(db, station, line):
    return db.scalar(
        select(StationLine.station_line_id).where(StationLine.station_id == station, StationLine.line_id == line)
    )


def _edge(db, a, a_line, b, b_line, edge_type="ADJACENT", source="OA-101"):
    db.add(
        StationEdge(
            from_station_line_id=_sl(db, a, a_line),
            to_station_line_id=_sl(db, b, b_line),
            edge_type=edge_type,
            source=source,
        )
    )
    db.flush()


def _elevator(db, eid, station_id, status=None, age=timedelta(0)):
    db.add(
        Elevator(
            elevator_id=eid, station_id=station_id, source="OA-15994", external_station_code=f"ST{station_id}",
            elevator_name=f"설비{eid}", location_description=None, operation_section=None,
        )
    )
    db.flush()
    if status:
        db.add(
            ElevatorStatus(
                elevator_id=eid, status=status, source_status_text=None, observed_at=None,
                fetched_at=to_utc_iso(utc_now() - age), source="OA-15994",
            )
        )
        db.flush()


@pytest.fixture
def graph(db):
    _edge(db, 3, "2", 5, "2")
    _edge(db, 5, "2", 6, "2")
    _edge(db, 5, "2", 5, "3", "TRANSFER", "OA-13290")
    _edge(db, 5, "3", 7, "3")
    _edge(db, 7, "3", 7, "1", "TRANSFER", "OA-13290")
    _edge(db, 7, "1", 2, "1")
    _edge(db, 2, "1", 1, "1")
    db.commit()
    return db


def route(client, a, b):
    return client.get("/api/routes", params={"from": a, "to": b})


# ---------- 순수 알고리즘 ----------


def test_shortest_path_prefers_cheaper_total_not_fewer_hops():
    graph = {
        1: [Neighbor(2, 10, "ADJACENT", None), Neighbor(3, 1, "ADJACENT", None)],
        3: [Neighbor(4, 1, "ADJACENT", None)],
        4: [Neighbor(2, 1, "ADJACENT", None)],
        2: [],
    }

    assert shortest_path(graph, [1], {2}) == (3, [1, 3, 4, 2])


def test_shortest_path_returns_none_when_unreachable_and_zero_for_same_node():
    graph = {1: [Neighbor(2, 2, "ADJACENT", None)], 2: [Neighbor(1, 2, "ADJACENT", None)], 3: []}

    assert shortest_path(graph, [1], {3}) is None
    assert shortest_path(graph, [1], {1}) == (0, [1])


def test_shortest_path_is_deterministic_on_ties():
    graph = {
        1: [Neighbor(2, 1, "ADJACENT", None), Neighbor(3, 1, "ADJACENT", None)],
        2: [Neighbor(4, 1, "ADJACENT", None)],
        3: [Neighbor(4, 1, "ADJACENT", None)],
    }

    results = {tuple(shortest_path(graph, [1], {4})[1]) for _ in range(20)}
    assert results == {(1, 2, 4)}


# ---------- API: 직선·환승 ----------


def test_straight_route_on_one_line(client, graph):
    body = route(client, 3, 6).json()

    assert [s["station_id"] for s in body["stations"]] == [3, 5, 6]
    assert body["transfer_count"] == 0
    assert body["total_minutes"] == 4  # 역 간 2분 x 2
    assert body["estimated"] is True
    assert [s["kind"] for s in body["timeline"]] == ["RIDE"]
    ride = body["timeline"][0]
    assert ride["line_id"] == "2" and ride["stops"] == 2 and ride["minutes"] == 4
    assert ride["from_station"]["station_id"] == 3 and ride["to_station"]["station_id"] == 6
    assert set(body) == {
        "from_station", "to_station", "stations", "transfer_count", "total_minutes", "estimated",
        "timeline", "elevator_warning", "limitations",
    }


def test_route_with_two_transfers(client, graph):
    body = route(client, 3, 2).json()

    assert [s["station_id"] for s in body["stations"]] == [3, 5, 7, 2]
    assert body["transfer_count"] == 2
    assert body["total_minutes"] == 2 + 5 + 2 + 5 + 2  # 역 간 3번, 환승 2번
    assert [s["kind"] for s in body["timeline"]] == ["RIDE", "TRANSFER", "RIDE", "TRANSFER", "RIDE"]
    assert [s["is_transfer"] for s in body["stations"]] == [False, True, True, False]
    transfer = body["timeline"][1]
    assert transfer["station"]["station_id"] == 5 and (transfer["from_line_id"], transfer["to_line_id"]) == ("2", "3")
    assert sum(s["minutes"] for s in body["timeline"]) == body["total_minutes"]


def test_route_works_in_both_directions(client, graph):
    forward = route(client, 3, 1).json()
    backward = route(client, 1, 3).json()

    assert [s["station_id"] for s in backward["stations"]] == [s["station_id"] for s in reversed(forward["stations"])]
    assert backward["total_minutes"] == forward["total_minutes"]


def test_weights_come_from_settings(client, graph, monkeypatch):
    monkeypatch.setattr(config.settings, "route_segment_minutes", 3)
    monkeypatch.setattr(config.settings, "route_transfer_minutes", 10)

    assert route(client, 3, 2).json()["total_minutes"] == 3 * 3 + 10 * 2


# ---------- API: 오류 ----------


def test_no_route_is_404_with_route_message(client, graph):
    response = route(client, 3, 9)

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "NOT_FOUND", "message": ROUTE_NOT_FOUND}}


def test_same_station_is_422(client, graph):
    response = route(client, 3, 3)

    assert response.status_code == 422
    assert response.json()["error"]["message"] == ROUTE_SAME_STATION


@pytest.mark.parametrize("a,b", [(999, 3), (3, 999), (0, 3), (3, -1)])
def test_unknown_station_is_404(client, graph, a, b):
    assert route(client, a, b).status_code == 404


@pytest.mark.parametrize("params", [{}, {"from": 3}, {"to": 3}, {"from": "x", "to": 3}, {"from": 3, "to": "1.5"}])
def test_missing_or_non_integer_params_are_422(client, graph, params):
    response = client.get("/api/routes", params=params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------- API: 한계 문구 ----------


def test_limitations_reflect_edge_sources(client, db):
    _edge(db, 3, "2", 5, "2", source="OA-101")
    _edge(db, 5, "2", 6, "2", source="OA-12035+FE-MOCK")
    db.commit()

    body = route(client, 3, 6).json()

    assert body["limitations"][0] == ROUTE_ESTIMATED
    assert ROUTE_EDGE_TIMETABLE in body["limitations"] and ROUTE_EDGE_PROVISIONAL in body["limitations"]
    assert body["limitations"][-1] == ROUTE_ACCESSIBILITY_UNVERIFIED
    assert LINE_1_PARTIAL not in body["limitations"]


def test_line_1_station_adds_partial_coverage_limitation(client, graph):
    assert LINE_1_PARTIAL in route(client, 3, 1).json()["limitations"]


# ---------- API: 엘리베이터 경고 ----------


def test_warning_is_unconfirmed_when_stations_have_no_elevator_data(client, graph):
    warning = route(client, 3, 6).json()["elevator_warning"]

    assert warning["status"] == "UNCONFIRMED" and warning["message"] == WARN_UNCONFIRMED
    assert [i["station_id"] for i in warning["items"]] == [3, 5, 6]
    assert {i["kind"] for i in warning["items"]} == {"UNCONFIRMED"}


def test_warning_lists_unavailable_and_partial_stations_in_route_order(client, graph):
    _elevator(graph, 301, 3, STOPPED)  # 강남: 전부 가동 안됨
    _elevator(graph, 501, 5, RUNNING)
    _elevator(graph, 502, 5, STOPPED)  # 교대: 일부 가동 안됨
    _elevator(graph, 601, 6, RUNNING)  # 서울대입구: 가동중
    graph.commit()

    warning = route(client, 3, 6).json()["elevator_warning"]

    assert warning["status"] == "WARNING" and warning["message"] == WARN_WARNING
    assert [(i["station_id"], i["kind"]) for i in warning["items"]] == [(3, "UNAVAILABLE"), (5, "PARTIAL")]


def test_warning_is_clear_only_when_every_route_station_is_confirmed_running(client, graph):
    for eid, sid in [(301, 3), (501, 5), (601, 6)]:
        _elevator(graph, eid, sid, RUNNING)
    graph.commit()

    warning = route(client, 3, 6).json()["elevator_warning"]

    assert warning == {"status": "CLEAR", "items": [], "message": WARN_CLEAR}
    assert "이용 가능" not in WARN_CLEAR  # 확인된 설비 기준이라고만 말한다


def test_stale_status_is_unconfirmed_not_clear(client, graph):
    _elevator(graph, 301, 3, RUNNING, age=timedelta(days=2))
    _elevator(graph, 501, 5, RUNNING)
    _elevator(graph, 601, 6, RUNNING)
    graph.commit()

    warning = route(client, 3, 6).json()["elevator_warning"]

    assert warning["status"] == "UNCONFIRMED"
    assert [i["station_id"] for i in warning["items"]] == [3]


def test_route_stations_carry_station_status_and_all_lines(client, graph):
    _elevator(graph, 501, 5, RUNNING)
    graph.commit()

    stations = {s["station_id"]: s for s in route(client, 3, 6).json()["stations"]}

    assert stations[5]["station_status"]["status"] == "AVAILABLE"
    assert [ln["line_id"] for ln in stations[5]["lines"]] == ["2", "3"]
    assert stations[3]["station_status"]["status"] is None


def test_route_does_not_change_the_database(client, graph):
    before = graph.scalar(select(StationEdge.edge_id).order_by(StationEdge.edge_id.desc()).limit(1))
    route(client, 3, 2)
    route(client, 3, 9)

    assert graph.scalar(select(StationEdge.edge_id).order_by(StationEdge.edge_id.desc()).limit(1)) == before
