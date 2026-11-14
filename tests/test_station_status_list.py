"""GET /api/stations/status 검증. 판정은 역별 API(/api/stations/{id}/elevators)와 같아야 한다."""

import pytest

from app.services.status_messages import GLOBAL_SOME_UNKNOWN, LINE_1_PARTIAL
from tests.conftest import STATION_FIXTURE
from tests.test_elevator_status_api import RUNNING, STALE_AGE, STOPPED, _add


@pytest.fixture
def seeded(db):
    _add(db, 101, 1, RUNNING)
    _add(db, 102, 1, STOPPED)
    _add(db, 103, 1, RUNNING, age=STALE_AGE)  # 역 1: PARTIAL, 미확인 1
    _add(db, 201, 2)
    _add(db, 202, 2, RUNNING, age=STALE_AGE)  # 역 2: null
    _add(db, 301, 3, RUNNING)
    _add(db, 302, 3, RUNNING)                 # 역 3: AVAILABLE
    _add(db, 501, 5, STOPPED)                 # 역 5: UNAVAILABLE
    db.commit()
    return db


def _bulk(client, query=""):
    return client.get(f"/api/stations/status{query}").json()


def test_shape_and_one_entry_per_station_sorted(client, seeded):
    r = client.get("/api/stations/status")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"stations", "count", "limitations"}
    ids = [s["station_id"] for s in body["stations"]]
    assert ids == sorted(s[0] for s in STATION_FIXTURE)
    assert body["count"] == len(ids)
    first = body["stations"][0]
    assert set(first) == {"station_id", "station_name", "lines", "station_status"}
    assert set(first["station_status"]) == {
        "status", "data_fresh", "total_elevator_count", "unknown_elevator_count"
    }
    assert first["lines"] == [
        {"line_id": "1", "line_name": "1호선"}, {"line_id": "4", "line_name": "4호선"}
    ]


def test_route_not_shadowed_by_station_id(client, seeded):
    assert client.get("/api/stations/status").status_code == 200
    assert client.get("/api/stations/abc").status_code == 422  # 다른 문자열은 여전히 station_id 파싱


def test_statuses_values(client, seeded):
    by_id = {s["station_id"]: s["station_status"] for s in _bulk(client)["stations"]}
    assert by_id[1]["status"] == "PARTIAL" and by_id[1]["data_fresh"] is False
    assert by_id[2]["status"] is None  # NO_DATA + STALE만 있으면 null
    assert by_id[3]["status"] == "AVAILABLE" and by_id[3]["data_fresh"] is True
    assert by_id[5]["status"] == "UNAVAILABLE"


def test_station_without_elevators(client, seeded):
    by_id = {s["station_id"]: s["station_status"] for s in _bulk(client)["stations"]}
    assert by_id[4] == {
        "status": None, "data_fresh": False, "total_elevator_count": 0, "unknown_elevator_count": 0
    }


def test_line_filter_keeps_full_lines_and_limitations(client, seeded):
    body = _bulk(client, "?line=2")
    assert [s["station_id"] for s in body["stations"]] == [2, 3, 5, 6, 9, 10]
    assert body["count"] == 6
    assert [ln["line_id"] for ln in body["stations"][0]["lines"]] == ["1", "2"]  # 전체 노선 유지
    assert LINE_1_PARTIAL in body["limitations"]  # 결과에 1호선 역(시청)이 있음
    assert GLOBAL_SOME_UNKNOWN in body["limitations"]


def test_line_without_line_1_stations_has_no_line_1_limitation(client, seeded):
    body = _bulk(client, "?line=8")
    assert [s["station_id"] for s in body["stations"]] == [10]
    assert LINE_1_PARTIAL not in body["limitations"]


def test_line_1_limitation_when_requested(client, db):
    assert _bulk(client, "?line=1")["limitations"] == [LINE_1_PARTIAL]  # 설비 없으면 미확인 문구 없음


def test_no_unknown_no_global_limitation(client, db):
    _add(db, 1001, 10, RUNNING)
    db.commit()
    assert _bulk(client, "?line=8")["limitations"] == []


@pytest.mark.parametrize("bad", ["0", "9", "x", "", "10", "-1"])
def test_invalid_line_is_422(client, seeded, bad):
    r = client.get("/api/stations/status", params={"line": bad})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_empty_db_all_null(client, db):
    body = _bulk(client)
    assert body["count"] == len(STATION_FIXTURE)
    assert all(s["station_status"]["status"] is None for s in body["stations"])


def test_equals_per_station_endpoint_for_every_station(client, seeded):
    bulk = {s["station_id"]: s["station_status"] for s in _bulk(client)["stations"]}
    for station_id, _name, _lines in STATION_FIXTURE:
        single = client.get(f"/api/stations/{station_id}/elevators").json()["station_status"]
        assert bulk[station_id] == single, station_id
