"""데모 DB 기반 스모크 테스트. FE/AI가 호출할 주요 API가 데모 DB에서 한 번씩 응답하는지만 본다.

DB는 임시 디렉터리에만 만든다(data/dev.db는 열지 않는다). 외부 API는 호출하지 않는다.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session, make_engine
from app.main import create_app
from app.models import Station
from app.services.demo_db import build_demo_db


@pytest.fixture(scope="module")
def smoke(tmp_path_factory):
    result = build_demo_db(tmp_path_factory.mktemp("smoke"), include_provisional_adjacent=True)
    engine = make_engine(f"sqlite:///{result.db_path}")
    session = Session(engine)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app), session
    session.close()
    engine.dispose()


def _station_id(session, name):
    return session.scalar(select(Station.station_id).where(Station.station_name == name).order_by(Station.station_id))


def test_health(smoke):
    client, _ = smoke
    response = client.get("/health")

    assert response.status_code == 200 and response.json() == {"status": "ok"}


def test_station_search_and_detail(smoke):
    client, session = smoke
    body = client.get("/api/stations", params={"q": "서울역"}).json()
    assert body["stations"], "서울역 검색 결과가 비어 있다"

    station_id = body["stations"][0]["station_id"]
    detail = client.get(f"/api/stations/{station_id}")
    assert detail.status_code == 200 and detail.json()["station_name"].startswith("서울역")


def test_elevator_status_endpoints(smoke):
    client, session = smoke
    station_id = _station_id(session, "서울역")

    by_station = client.get(f"/api/stations/{station_id}/elevators")
    assert by_station.status_code == 200
    assert "limitations" in by_station.json() and "station_status" in by_station.json()

    overall = client.get("/api/elevators/status")
    assert overall.status_code == 200 and overall.json()["count"] > 0


def test_alternatives(smoke):
    client, session = smoke
    response = client.get(f"/api/stations/{_station_id(session, '서울역')}/alternatives")

    body = response.json()
    assert response.status_code == 200
    assert body["limitations"] and "alternatives" in body


def test_departure_time(smoke):
    client, _ = smoke
    response = client.post(
        "/api/departure-time",
        json={"arrival_at": "2099-01-01T09:00:00+09:00", "travel_minutes": 30},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["estimated"] is True and body["timetable_used"] is False and body["elevator_warning"]["status"] == "UNCONFIRMED"


def test_arrivals_are_unconfirmed_without_provider(smoke):
    client, session = smoke
    response = client.get(f"/api/stations/{_station_id(session, '서울역')}/arrivals")

    body = response.json()
    assert response.status_code == 200
    assert body["arrival_status"] == "UNCONFIRMED" and body["arrivals"] == []


def test_error_format(smoke):
    client, _ = smoke
    response = client.get("/api/stations/999999")

    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"


def test_route_between_real_demo_stations(smoke):
    client, session = smoke
    a = _station_id(session, "서울역")
    alternatives = client.get(f"/api/stations/{a}/alternatives").json()["alternatives"]
    # 대체역 후보가 없어도(전부 미확인) 인접 간선으로 닿는 역은 경로가 있어야 한다. 서울역 -> 시청
    b = alternatives[0]["station_id"] if alternatives else _station_id(session, "시청")

    response = client.get("/api/routes", params={"from": a, "to": b})

    body = response.json()
    assert response.status_code == 200, body
    assert body["estimated"] is True and body["total_minutes"] > 0
    assert body["elevator_warning"]["status"] in {"WARNING", "UNCONFIRMED", "CLEAR"}
    assert body["limitations"]


def test_status_list_has_lines_and_line_filter(smoke):
    client, _ = smoke
    body = client.get("/api/elevators/status", params={"line": "2"}).json()

    assert body["count"] > 0
    assert all(any(ln["line_id"] == "2" for ln in e["lines"]) for e in body["elevators"])


def test_departure_time_with_stations_uses_the_demo_graph(smoke):
    client, session = smoke
    response = client.post("/api/departure-time", json={
        "arrival_at": "2099-01-01T09:00:00+09:00", "travel_minutes": 30,
        "from_station_id": _station_id(session, "서울역"), "to_station_id": _station_id(session, "시청"),
    })

    assert response.status_code == 200
    assert response.json()["elevator_warning"]["status"] in {"WARNING", "UNCONFIRMED", "CLEAR"}
