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
