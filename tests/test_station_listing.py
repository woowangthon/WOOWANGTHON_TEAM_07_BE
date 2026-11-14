"""GET /api/stations의 q 생략(전체 목록) 검증 (#73).

임시 SQLite DB(마이그레이션 적용본)만 쓴다. 개발 DB(data/dev.db)와 외부 API는 쓰지 않는다.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import get_session
from app.main import create_app
from app.models import Line, Station, StationLine
from app.services.stations import LINE_1_LIMITATION
from tests.conftest import STATION_FIXTURE


def names(body) -> list[str]:
    return [s["station_name"] for s in body["stations"]]


def ids(body) -> list[int]:
    return [s["station_id"] for s in body["stations"]]


def _client_for(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def _seed(session: Session, stations: list[tuple[int, str, list[str]]]) -> None:
    for n in range(1, 9):
        session.add(Line(line_id=str(n), line_name=f"{n}호선", operator=None))
    session.flush()
    for station_id, name, _lines in stations:
        session.add(Station(station_id=station_id, station_name=name))
    session.flush()
    for station_id, _name, lines in stations:
        for line_id in lines:
            session.add(StationLine(station_id=station_id, line_id=line_id))
    session.commit()


# ---------- q 생략: 전체 목록 ----------


def test_omitted_q_returns_every_station_once_sorted(client):
    response = client.get("/api/stations")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"stations", "count", "limitations"}
    assert body["count"] == len(STATION_FIXTURE) == len(body["stations"])
    # 환승역도 한 번만 나온다
    assert sorted(ids(body)) == sorted(sid for sid, _n, _l in STATION_FIXTURE)
    assert len(set(ids(body))) == len(ids(body))
    # 역명 오름차순, 같은 역명은 station_id 오름차순
    expected = sorted(((name, sid) for sid, name, _l in STATION_FIXTURE))
    assert [(s["station_name"], s["station_id"]) for s in body["stations"]] == expected
    # 1호선 역이 포함되므로 표준 문구
    assert body["limitations"] == [LINE_1_LIMITATION]


def test_omitted_q_returns_full_line_list_for_transfer_station(client):
    body = client.get("/api/stations").json()

    jongno = next(s for s in body["stations"] if s["station_name"] == "종로3가")
    assert [ln["line_id"] for ln in jongno["lines"]] == ["1", "3", "5"]
    ordering = next(s for s in body["stations"] if s["station_name"] == "정렬시험")
    assert [ln["line_id"] for ln in ordering["lines"]] == ["2", "8"]


@pytest.mark.parametrize("line", [str(n) for n in range(1, 9)])
def test_omitted_q_with_line_filter(client, line):
    response = client.get("/api/stations", params={"line": line})

    assert response.status_code == 200
    body = response.json()
    expected = sorted((name, sid) for sid, name, lines in STATION_FIXTURE if line in lines)
    assert [(s["station_name"], s["station_id"]) for s in body["stations"]] == expected
    assert body["count"] == len(expected)
    for s in body["stations"]:  # 응답 lines는 필터와 무관하게 전체 노선
        fixture_lines = next(lines for sid, _n, lines in STATION_FIXTURE if sid == s["station_id"])
        assert [ln["line_id"] for ln in s["lines"]] == sorted(fixture_lines, key=int)
    if line == "1":
        assert body["limitations"] == [LINE_1_LIMITATION]
    else:
        has_line_1 = any(ln["line_id"] == "1" for s in body["stations"] for ln in s["lines"])
        assert body["limitations"] == ([LINE_1_LIMITATION] if has_line_1 else [])


def test_omitted_q_line_without_stations_is_empty_200(client):
    # 픽스처에는 6호선 역이 없다
    body = client.get("/api/stations", params={"line": "6"}).json()
    assert body == {"stations": [], "count": 0, "limitations": []}


def test_omitted_q_line_1_without_results_keeps_line_1_limitation(engine):
    with Session(engine) as session:
        _seed(session, [(1, "강남", ["2"])])
        body = _client_for(session).get("/api/stations", params={"line": "1"}).json()

    assert body == {"stations": [], "count": 0, "limitations": [LINE_1_LIMITATION]}


@pytest.mark.parametrize("line", ["0", "9", "a", ""])
def test_omitted_q_invalid_line_is_422(client, line):
    response = client.get("/api/stations", params={"line": line})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_same_name_stations_are_separate_and_ordered_by_id(engine):
    with Session(engine) as session:
        _seed(session, [(30, "양평", ["5"]), (12, "양평", ["8"]), (20, "가락", ["8"])])
        body = _client_for(session).get("/api/stations").json()

    assert [(s["station_name"], s["station_id"]) for s in body["stations"]] == [
        ("가락", 20),
        ("양평", 12),
        ("양평", 30),
    ]
    assert body["limitations"] == []


def test_non_contiguous_ids_are_all_listed(engine):
    stations = [(3, "가", ["2"]), (17, "나", ["3"]), (250, "다", ["4"]), (1001, "라", ["2", "4"])]
    with Session(engine) as session:
        _seed(session, stations)
        body = _client_for(session).get("/api/stations").json()

    assert ids(body) == [3, 17, 250, 1001]  # 빈 ID(1, 2, 4...)를 채우거나 건너뛰지 않는다
    assert all(isinstance(i, int) for i in ids(body))
    assert body["count"] == 4


def test_empty_db_returns_empty_200(engine):
    with Session(engine) as session:
        client = _client_for(session)
        all_body = client.get("/api/stations").json()
        line_body = client.get("/api/stations", params={"line": "2"}).json()
        response = client.get("/api/stations")

    assert response.status_code == 200
    assert all_body == {"stations": [], "count": 0, "limitations": []}
    assert line_body == {"stations": [], "count": 0, "limitations": []}


# ---------- q 명시: 기존 규칙 유지 ----------


@pytest.mark.parametrize("raw", ["q=", "q=%20", "q=%20%20%20", "q=%09", "q=&line=2"])
def test_explicit_blank_q_is_still_422(client, raw):
    response = client.get(f"/api/stations?{raw}")

    assert response.status_code == 422
    assert set(response.json()) == {"error"}
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_explicit_q_over_20_chars_is_still_422(client):
    response = client.get("/api/stations", params={"q": "가" * 21})
    assert response.status_code == 422


@pytest.mark.parametrize("q", ["%", "_", "%%", "\\", "'", "\"", "*", "?", "(", ")"])
def test_special_characters_are_literal_not_wildcards(client, q):
    response = client.get("/api/stations", params={"q": q})

    assert response.status_code == 200
    body = response.json()
    expected = [name for _sid, name, _l in STATION_FIXTURE if q in name]
    assert sorted(names(body)) == sorted(expected)


def test_parenthesis_and_comma_search_hits_literal_name(client):
    for q in ["(법원", "법원, 검찰청", ")"]:
        body = client.get("/api/stations", params={"q": q}).json()
        assert "교대(법원, 검찰청)" in names(body)


def test_percent_and_underscore_match_only_literal(engine):
    with Session(engine) as session:
        _seed(session, [(1, "백%역", ["2"]), (2, "백_역", ["2"]), (3, "백화역", ["2"])])
        client = _client_for(session)
        percent = client.get("/api/stations", params={"q": "%"}).json()
        underscore = client.get("/api/stations", params={"q": "백_"}).json()

    assert names(percent) == ["백%역"]
    assert names(underscore) == ["백_역"]


def test_search_regression_with_q(client):
    body = client.get("/api/stations", params={"q": " 서울 "}).json()
    assert names(body) == ["서울대입구(관악구청)", "서울역"]

    body = client.get("/api/stations", params={"q": "신도림"}).json()
    assert ids(body) == [8, 9]

    body = client.get("/api/stations", params={"q": "서울", "line": "4"}).json()
    assert names(body) == ["서울역"]
    assert [ln["line_id"] for ln in body["stations"][0]["lines"]] == ["1", "4"]

    body = client.get("/api/stations", params={"q": "없는역"}).json()
    assert body == {"stations": [], "count": 0, "limitations": []}


def test_q_result_is_subset_of_full_list(client):
    full = client.get("/api/stations").json()["stations"]
    searched = client.get("/api/stations", params={"q": "서울"}).json()["stations"]
    assert all(s in full for s in searched)


# ---------- OpenAPI ----------


def test_openapi_marks_q_optional(client):
    schema = client.get("/openapi.json").json()
    params = {p["name"]: p for p in schema["paths"]["/api/stations"]["get"]["parameters"]}

    assert params["q"].get("required", False) is False
    assert params["line"].get("required", False) is False
    assert "생략" in params["q"]["description"]
