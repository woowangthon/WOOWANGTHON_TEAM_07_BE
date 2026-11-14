"""AI가 쓰는 5개 기능(상태, 대체역, 출발 시각, 고장·점검 목록, 경로)이 함께 동작하는지 보는 통합 시나리오. 이슈 #42.

그래프(역 ID, 괄호는 노선): 3강남(2) - 5교대(2) - 6서울대입구(2), 5교대(2) = 5교대(3) - 7종로3가(3), 9신도림(2)는 고립.
설비: 강남 전부 가동 안됨, 교대 일부 가동 안됨, 서울대입구·종로3가 가동중, 신도림 정보 없음.
"""

import pytest
from sqlalchemy import select

from app.models import Elevator, ElevatorStatus, StationEdge, StationLine
from app.timeutil import to_utc_iso, utc_now

RUNNING, STOPPED = "가동중", "가동 안됨"


def _sl(db, station, line):
    return db.scalar(select(StationLine.station_line_id).where(StationLine.station_id == station, StationLine.line_id == line))


def _edge(db, a, al, b, bl, edge_type="ADJACENT"):
    db.add(StationEdge(from_station_line_id=_sl(db, a, al), to_station_line_id=_sl(db, b, bl), edge_type=edge_type,
                       source="OA-13290" if edge_type == "TRANSFER" else "OA-101"))
    db.flush()


def _elevator(db, eid, station_id, status):
    db.add(Elevator(elevator_id=eid, station_id=station_id, source="OA-15994", external_station_code=f"ST{station_id}",
                    elevator_name=f"설비{eid}", location_description=f"{station_id}번 출구", operation_section="B1-1"))
    db.flush()
    db.add(ElevatorStatus(elevator_id=eid, status=status, source_status_text=None, observed_at=None,
                          fetched_at=to_utc_iso(utc_now()), source="OA-15994"))
    db.flush()


@pytest.fixture
def world(db):
    _edge(db, 3, "2", 5, "2")
    _edge(db, 5, "2", 6, "2")
    _edge(db, 5, "2", 5, "3", "TRANSFER")
    _edge(db, 5, "3", 7, "3")
    _elevator(db, 301, 3, STOPPED)
    _elevator(db, 501, 5, RUNNING)
    _elevator(db, 502, 5, STOPPED)
    _elevator(db, 601, 6, RUNNING)
    _elevator(db, 701, 7, RUNNING)
    db.commit()
    return db


def departure(client, **body):
    base = {"arrival_at": "2026-11-13T09:00:00+09:00", "travel_minutes": 20}
    base.update(body)
    return client.post("/api/departure-time", json=base)


# ---------- 시나리오 1: 엘리베이터 상태 ----------


def test_scenario_station_status(client, world):
    body = client.get("/api/stations/3/elevators").json()

    assert body["station_status"]["status"] == "UNAVAILABLE"
    assert body["elevators"][0]["status"] == STOPPED and body["elevators"][0]["location_description"] == "3번 출구"
    assert client.get("/api/stations/5/elevators").json()["station_status"]["status"] == "PARTIAL"
    assert client.get("/api/stations/9/elevators").json()["station_status"]["status"] is None  # 설비 없음은 판정하지 않는다


# ---------- 시나리오 2: 대체역 ----------


def test_scenario_alternatives_for_a_broken_station(client, world):
    body = client.get("/api/stations/3/alternatives").json()

    assert [a["station_id"] for a in body["alternatives"]] == [5, 6, 7]
    assert body["alternatives"][0]["same_line"] is True and body["alternatives"][0]["station_status"]["status"] == "PARTIAL"
    assert body["limitations"]  # 검증되지 않았다는 문구가 항상 붙는다


# ---------- 시나리오 3: 출발 시각 ----------


def test_scenario_departure_time_without_stations_stays_unconfirmed(client, world):
    body = departure(client).json()

    assert body["elevator_warning"]["status"] == "UNCONFIRMED" and body["elevator_warning"]["items"] == []
    assert body["inputs"]["buffer_minutes"] == 5 and "buffer_minutes" in body["defaults_applied"]
    assert body["estimated"] is True and body["timetable_used"] is False


def test_scenario_departure_time_with_stations_warns_about_the_route(client, world):
    body = departure(client, from_station_id=3, to_station_id=6).json()

    warning = body["elevator_warning"]
    assert warning["status"] == "WARNING"
    assert [(i["station_id"], i["kind"]) for i in warning["items"]] == [(3, "UNAVAILABLE"), (5, "PARTIAL")]
    assert warning["message"] in body["limitations"]


def test_scenario_departure_time_with_a_clear_route(client, world):
    body = departure(client, from_station_id=6, to_station_id=5).json()  # 서울대입구(가동중) -> 교대(일부 가동 안됨)
    assert body["elevator_warning"]["status"] == "WARNING"

    body = departure(client, from_station_id=7, to_station_id=5).json()  # 종로3가 -> 교대 (환승)
    assert body["elevator_warning"]["status"] == "WARNING"


def test_scenario_departure_time_station_id_rules(client, world):
    assert departure(client, from_station_id=3).status_code == 422
    assert departure(client, to_station_id=3).status_code == 422
    assert departure(client, from_station_id=3, to_station_id=3).status_code == 422
    assert departure(client, from_station_id=3, to_station_id=999).status_code == 404
    assert departure(client, from_station_id=0, to_station_id=3).status_code == 404
    assert departure(client, from_station_id="3", to_station_id=6).status_code == 422  # 정수만 받는다


def test_scenario_departure_time_with_no_route_is_unconfirmed_not_an_error(client, world):
    body = departure(client, from_station_id=3, to_station_id=9)

    assert body.status_code == 200
    assert body.json()["elevator_warning"]["status"] == "UNCONFIRMED"
    assert body.json()["elevator_warning"]["items"] == []


def test_departure_time_travel_minutes_is_still_external_input(client, world):
    first = departure(client, from_station_id=3, to_station_id=6, travel_minutes=20).json()
    second = departure(client, from_station_id=3, to_station_id=6, travel_minutes=40).json()

    assert second["total_minutes"] - first["total_minutes"] == 20  # 경로 소요 시간으로 덮어쓰지 않는다


# ---------- 시나리오 4: 고장·점검 목록 ----------


def test_scenario_broken_list_with_line_filter(client, world):
    body = client.get("/api/elevators/status", params={"status": STOPPED, "line": "2"}).json()

    assert [e["elevator_id"] for e in body["elevators"]] == [301, 502]
    assert all("2" in [ln["line_id"] for ln in e["lines"]] for e in body["elevators"])
    only_line_3 = client.get("/api/elevators/status", params={"status": STOPPED, "line": "3"}).json()
    assert [e["elevator_id"] for e in only_line_3["elevators"]] == [502]  # 교대는 2·3호선 환승역
    assert [ln["line_id"] for ln in only_line_3["elevators"][0]["lines"]] == ["2", "3"]  # 필터와 무관하게 전체 노선


@pytest.mark.parametrize("line", ["0", "9", "10", "2호선", "", "a"])
def test_scenario_line_filter_validation(client, world, line):
    response = client.get("/api/elevators/status", params={"line": line})

    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_scenario_line_filter_without_matches_is_empty_not_error(client, world):
    body = client.get("/api/elevators/status", params={"line": "8"}).json()

    assert body["elevators"] == [] and body["count"] == 0


# ---------- 시나리오 5: 경로 ----------


def test_scenario_route_between_stations(client, world):
    body = client.get("/api/routes", params={"from": 3, "to": 7}).json()

    assert [s["station_id"] for s in body["stations"]] == [3, 5, 7]
    assert body["transfer_count"] == 1 and body["estimated"] is True
    assert body["elevator_warning"]["status"] == "WARNING"


# ---------- 흐름: 고장 -> 대체역 -> 경로 -> 출발 시각 ----------


def test_full_flow_from_broken_station_to_departure_time(client, world):
    status = client.get("/api/stations/3/elevators").json()["station_status"]["status"]
    assert status == "UNAVAILABLE"

    alternative = client.get("/api/stations/3/alternatives").json()["alternatives"][0]["station_id"]
    route = client.get("/api/routes", params={"from": alternative, "to": 7}).json()
    plan = departure(client, from_station_id=alternative, to_station_id=7, travel_minutes=route["total_minutes"]).json()

    assert alternative == 5 and route["total_minutes"] == 2  # 출발역의 모든 노선에서 시작하므로 환승 없이 역 간 2분
    assert plan["total_minutes"] == 2 + 7 + 3 + 5 + 3  # 이동 + 도보 + 진입 + 대기 + 출구
    assert plan["elevator_warning"]["status"] == "WARNING"
