"""GET /api/stations/{id}/elevators, GET /api/elevators/status 검증.

임시 SQLite DB(마이그레이션 적용본)에 설비와 상태 행을 넣어 쓴다. 개발 DB(data/dev.db)는 열지 않는다.
시드 역 ID는 tests/conftest.py의 STATION_FIXTURE를 따른다.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app import config
from app.models import Elevator, ElevatorStatus
from app.services.status_messages import NO_CONFIRMED, NO_ELEVATORS, PARTIAL_UNKNOWN
from app.timeutil import to_utc_iso, utc_now

RUNNING = "가동중"
STOPPED = "가동 안됨"


def _add(db, elevator_id, station_id, status=None, age=timedelta(0)):
    """설비 한 개와(status가 있으면) 상태 행 한 개를 넣는다. age만큼 과거에 가져온 것으로 적는다."""
    db.add(
        Elevator(
            elevator_id=elevator_id,
            station_id=station_id,
            source="OA-15994",
            external_station_code=f"ST{station_id}",
            elevator_name=f"설비{elevator_id}",
            location_description="2번 출입구",
            operation_section="B1-1F",
        )
    )
    db.flush()
    if status is not None:
        db.add(
            ElevatorStatus(
                elevator_id=elevator_id,
                status=status,
                source_status_text=None,
                observed_at=None,
                fetched_at=to_utc_iso(utc_now() - age),
                source="OA-15994",
            )
        )
        db.flush()


STALE_AGE = timedelta(hours=2)  # 기본 TTL 30분을 넘긴 값


@pytest.fixture
def seeded(db):
    _add(db, 101, 1, RUNNING)
    _add(db, 102, 1, STOPPED)
    _add(db, 103, 1, RUNNING, age=STALE_AGE)  # 역 1: 확인 2 + 오래됨 1 -> PARTIAL, 미확인 1
    _add(db, 201, 2)                          # 행 없음 (NO_DATA)
    _add(db, 202, 2, RUNNING, age=STALE_AGE)  # 역 2: 확인 0 -> status null
    _add(db, 301, 3, RUNNING)
    _add(db, 302, 3, RUNNING)                 # 역 3: 전부 가동중 -> AVAILABLE
    _add(db, 501, 5, STOPPED)                 # 역 5: 전부 가동 안됨 -> UNAVAILABLE
    _add(db, 601, 6, RUNNING, age=timedelta(minutes=5))  # TTL 설정 테스트용
    db.commit()
    return db


# ---------- 역별 엘리베이터 목록 ----------


def test_mixed_station_is_partial_and_not_fresh(client, seeded):
    body = client.get("/api/stations/1/elevators").json()

    assert body["station_status"] == {
        "status": "PARTIAL",
        "data_fresh": False,
        "total_elevator_count": 3,
        "unknown_elevator_count": 1,
    }
    assert body["limitations"] == [PARTIAL_UNKNOWN]
    by_id = {e["elevator_id"]: e for e in body["elevators"]}
    assert by_id[101]["status"] == RUNNING and by_id[101]["data_fresh"] is True
    assert by_id[101]["unknown_reason"] is None
    assert by_id[102]["status"] == STOPPED
    assert by_id[103]["status"] is None
    assert by_id[103]["data_fresh"] is False
    assert by_id[103]["unknown_reason"] == "STALE"


def test_station_with_no_confirmed_elevators_has_null_status(client, seeded):
    body = client.get("/api/stations/2/elevators").json()

    assert body["station_status"] == {
        "status": None,
        "data_fresh": False,
        "total_elevator_count": 2,
        "unknown_elevator_count": 2,
    }
    assert body["limitations"] == [NO_CONFIRMED]
    reasons = {e["elevator_id"]: e["unknown_reason"] for e in body["elevators"]}
    assert reasons == {201: "NO_DATA", 202: "STALE"}
    assert all(e["status"] is None for e in body["elevators"])


def test_all_running_station_is_available_and_fresh(client, seeded):
    body = client.get("/api/stations/3/elevators").json()

    assert body["station_status"]["status"] == "AVAILABLE"
    assert body["station_status"]["data_fresh"] is True
    assert body["station_status"]["unknown_elevator_count"] == 0
    assert body["limitations"] == []


def test_all_stopped_station_is_unavailable(client, seeded):
    body = client.get("/api/stations/5/elevators").json()

    assert body["station_status"]["status"] == "UNAVAILABLE"
    assert body["station_status"]["data_fresh"] is True
    assert body["limitations"] == []


def test_station_without_elevators_returns_200_with_standard_message(client, seeded):
    response = client.get("/api/stations/4/elevators")

    assert response.status_code == 200
    body = response.json()
    assert body["station_status"] == {
        "status": None,
        "data_fresh": False,
        "total_elevator_count": 0,
        "unknown_elevator_count": 0,
    }
    assert body["limitations"] == [NO_ELEVATORS]
    assert body["elevators"] == []


@pytest.mark.parametrize("station_id", [99, 0, -1])
def test_missing_or_non_positive_station_is_404(client, seeded, station_id):
    response = client.get(f"/api/stations/{station_id}/elevators")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_non_integer_station_id_is_422(client, seeded):
    response = client.get("/api/stations/abc/elevators")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_station_response_fields_match_contract(client, seeded):
    body = client.get("/api/stations/1/elevators").json()

    assert set(body) == {"station", "station_status", "limitations", "elevators"}
    assert set(body["station"]) == {"station_id", "station_name"}
    assert set(body["station_status"]) == {
        "status", "data_fresh", "total_elevator_count", "unknown_elevator_count",
    }
    assert set(body["elevators"][0]) == {
        "elevator_id", "elevator_name", "location_description", "operation_section",
        "status", "data_fresh", "unknown_reason", "observed_at", "fetched_at",
    }


def test_elevators_are_ordered_by_elevator_id(client, seeded):
    body = client.get("/api/stations/1/elevators").json()

    assert [e["elevator_id"] for e in body["elevators"]] == [101, 102, 103]


def test_fetched_at_is_kst_and_observed_at_is_null(client, seeded):
    body = client.get("/api/stations/3/elevators").json()

    for elevator in body["elevators"]:
        assert elevator["observed_at"] is None
        parsed = datetime.fromisoformat(elevator["fetched_at"])
        assert parsed.utcoffset() == timedelta(hours=9)


def test_no_data_elevator_has_null_fetched_at(client, seeded):
    body = client.get("/api/stations/2/elevators").json()

    no_data = next(e for e in body["elevators"] if e["elevator_id"] == 201)
    assert no_data["fetched_at"] is None
    assert no_data["unknown_reason"] == "NO_DATA"


def test_ttl_comes_from_settings(client, seeded, monkeypatch):
    # 역 6의 설비는 5분 전에 가져왔다. 기본 TTL 30분 안이므로 신선하다.
    fresh = client.get("/api/stations/6/elevators").json()
    assert fresh["elevators"][0]["data_fresh"] is True
    assert fresh["station_status"]["status"] == "AVAILABLE"

    # TTL을 1분으로 줄이면 같은 행이 오래된 것이 된다.
    monkeypatch.setattr(config.settings, "elevator_status_ttl_minutes", 1)
    stale = client.get("/api/stations/6/elevators").json()
    assert stale["elevators"][0]["status"] is None
    assert stale["elevators"][0]["unknown_reason"] == "STALE"
    assert stale["station_status"]["status"] is None


# ---------- 전체 설비 상태 목록 ----------


def test_global_list_includes_every_elevator_with_station_name(client, seeded):
    response = client.get("/api/elevators/status")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"elevators", "count", "limitations"}
    assert body["count"] == len(body["elevators"]) == 9
    assert body["limitations"] == []
    first = body["elevators"][0]
    assert set(first) == {
        "elevator_id", "elevator_name", "station_id", "station_name",
        "status", "data_fresh", "unknown_reason", "observed_at", "fetched_at",
    }
    assert first["station_name"] == "서울역"


def test_status_filter_keeps_only_confirmed_matches(client, seeded):
    stopped = client.get("/api/elevators/status", params={"status": STOPPED}).json()
    running = client.get("/api/elevators/status", params={"status": RUNNING}).json()

    assert [e["elevator_id"] for e in stopped["elevators"]] == [102, 501]
    # 오래된 103, 202와 행 없는 201은 어떤 필터에도 들어가지 않는다.
    assert [e["elevator_id"] for e in running["elevators"]] == [101, 301, 302, 601]
    assert stopped["count"] == 2


def test_status_filter_accepts_percent_encoded_value(client, seeded):
    # "가동 안됨"의 퍼센트 인코딩 형태(가동·공백·안됨)
    response = client.get("/api/elevators/status?status=%EA%B0%80%EB%8F%99%20%EC%95%88%EB%90%A8")

    assert response.status_code == 200
    assert response.json()["count"] == 2


@pytest.mark.parametrize("bad", ["고장", "보수중", "가동중 ", "UNKNOWN", ""])
def test_invalid_status_filter_is_422(client, seeded, bad):
    response = client.get("/api/elevators/status", params={"status": bad})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_global_list_is_empty_when_no_elevators(client):
    body = client.get("/api/elevators/status").json()

    assert body == {"elevators": [], "count": 0, "limitations": []}


# ---------- DB 조회 횟수 (N+1 방지) ----------


def _count_selects(db, client, url):
    statements = []

    def before(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", before)
    try:
        db.expunge_all()  # 식별자 맵 캐시가 조회 횟수를 바꾸지 않도록 비운다
        client.get(url)
    finally:
        event.remove(db.bind, "before_cursor_execute", before)
    return len(statements)


def test_station_query_count_does_not_grow_with_elevators(client, seeded):
    three_elevators = _count_selects(seeded, client, "/api/stations/1/elevators")
    two_elevators = _count_selects(seeded, client, "/api/stations/3/elevators")

    assert three_elevators == two_elevators
    assert three_elevators <= 2  # 역 1건 + 설비·상태 조인 1건


def test_global_query_is_a_single_select(client, seeded):
    assert _count_selects(seeded, client, "/api/elevators/status") == 1
