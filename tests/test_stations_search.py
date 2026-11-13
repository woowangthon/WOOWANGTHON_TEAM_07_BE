"""GET /api/stations 검증. 임시 SQLite DB(마이그레이션 적용본)에 픽스처를 넣어 쓴다.

개발 DB(data/dev.db)는 열지 않는다. 의존성 get_session을 임시 DB 세션으로 바꿔서 쓴다.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import get_session
from app.main import create_app
from app.models import Line, Station, StationLine
from app.services.stations import LINE_1_LIMITATION

# 픽스처: 역 id, 역명, 노선. 실제 역 마스터가 아니라 검색·필터·정렬 규칙을 보려는 시험 데이터다.
FIXTURE = [
    (1, "서울역", ["1", "4"]),          # 환승역
    (2, "시청", ["1", "2"]),            # 환승역
    (3, "강남", ["2"]),
    (4, "동묘앞", ["1"]),
    (5, "교대(법원, 검찰청)", ["2", "3"]),  # 환승역, 괄호 포함 이름
    (6, "서울대입구(관악구청)", ["2"]),
    (9, "신도림", ["2"]),                # 같은 역명을 두 개 둔다(정렬 확인)
    (8, "신도림", ["1"]),
]


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        for n in range(1, 9):
            session.add(Line(line_id=str(n), line_name=f"{n}호선", operator=None))
        session.flush()
        for station_id, name, lines in FIXTURE:
            session.add(Station(station_id=station_id, station_name=name))
        session.flush()
        for station_id, _, lines in FIXTURE:
            for line_id in lines:
                session.add(StationLine(station_id=station_id, line_id=line_id))
        session.commit()
        yield session


@pytest.fixture
def client(db):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db
    return TestClient(app)


def names(body) -> list[str]:
    return [s["station_name"] for s in body["stations"]]


def test_search_returns_stations_with_all_their_lines(client):
    response = client.get("/api/stations", params={"q": "서울"})

    assert response.status_code == 200
    body = response.json()
    assert names(body) == ["서울대입구(관악구청)", "서울역"]  # 역명 오름차순
    seoul = next(s for s in body["stations"] if s["station_name"] == "서울역")
    assert seoul["lines"] == [
        {"line_id": "1", "line_name": "1호선"},
        {"line_id": "4", "line_name": "4호선"},
    ]
    assert body["count"] == 2


def test_response_fields_match_contract_exactly(client):
    body = client.get("/api/stations", params={"q": "서울"}).json()

    assert set(body) == {"stations", "count", "limitations"}
    assert set(body["stations"][0]) == {"station_id", "station_name", "lines"}
    assert set(body["stations"][0]["lines"][0]) == {"line_id", "line_name"}


def test_query_is_trimmed_before_search(client):
    plain = client.get("/api/stations", params={"q": "서울역"}).json()
    padded = client.get("/api/stations", params={"q": "   서울역  "})

    assert padded.status_code == 200
    assert padded.json() == plain


def test_query_length_limits(client):
    twenty = "가" * 20
    assert client.get("/api/stations", params={"q": twenty}).status_code == 200
    assert client.get("/api/stations", params={"q": " " + twenty + " "}).status_code == 200  # 공백 제거 후 20자

    too_long = client.get("/api/stations", params={"q": "가" * 21})
    assert too_long.status_code == 422
    assert too_long.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("q", ["", "   ", "\t \n"])
def test_empty_query_after_trim_is_rejected(client, q):
    response = client.get("/api/stations", params={"q": q})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_missing_query_is_rejected_with_common_error(client):
    response = client.get("/api/stations")

    assert response.status_code == 422
    assert set(response.json()) == {"error"}
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_line_filter_keeps_full_line_list(client):
    body = client.get("/api/stations", params={"q": "서울", "line": "4"}).json()

    assert names(body) == ["서울역"]  # 4호선을 지나는 역만
    assert [ln["line_id"] for ln in body["stations"][0]["lines"]] == ["1", "4"]  # 노선은 전체


def test_line_filter_excludes_stations_without_that_line(client):
    body = client.get("/api/stations", params={"q": "서울", "line": "2"}).json()

    assert names(body) == ["서울대입구(관악구청)"]


@pytest.mark.parametrize("line", ["0", "9", "10", "1호선", "01"])
def test_invalid_line_filter_is_rejected(client, line):
    response = client.get("/api/stations", params={"q": "서울", "line": line})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_zero_results_return_200_with_empty_array(client):
    response = client.get("/api/stations", params={"q": "없는역이름"})

    assert response.status_code == 200
    assert response.json() == {"stations": [], "count": 0, "limitations": []}


def test_transfer_station_appears_once_with_all_lines(client):
    body = client.get("/api/stations", params={"q": "시청"}).json()

    assert names(body) == ["시청"]  # 하나의 station_id로 한 번만
    assert [ln["line_id"] for ln in body["stations"][0]["lines"]] == ["1", "2"]


def test_transfer_station_not_duplicated_by_line_filter(client):
    for line in ("1", "2"):
        body = client.get("/api/stations", params={"q": "시청", "line": line}).json()
        assert names(body) == ["시청"]
        assert body["count"] == 1


def test_same_name_stations_sorted_by_station_id(client):
    body = client.get("/api/stations", params={"q": "신도림"}).json()

    assert [s["station_id"] for s in body["stations"]] == [8, 9]  # 입력 순서(9, 8)와 무관


def test_full_result_order_is_name_then_id(client):
    body = client.get("/api/stations", params={"q": "역"}).json()

    keys = [(s["station_name"], s["station_id"]) for s in body["stations"]]
    assert keys == sorted(keys)


def test_line_1_limitation_when_line_1_station_in_results(client):
    body = client.get("/api/stations", params={"q": "동묘앞"}).json()

    assert names(body) == ["동묘앞"]
    assert body["limitations"] == [LINE_1_LIMITATION]


def test_line_1_limitation_when_line_1_requested_even_without_results(client):
    body = client.get("/api/stations", params={"q": "없는역", "line": "1"}).json()

    assert body == {"stations": [], "count": 0, "limitations": [LINE_1_LIMITATION]}


def test_no_limitation_when_no_line_1_station_is_involved(client):
    body = client.get("/api/stations", params={"q": "강남"}).json()

    assert body["limitations"] == []


def test_limitation_present_when_transfer_station_includes_line_1(client):
    # 시청은 1호선 역이므로 2호선 필터로 조회해도 1호선 한계를 알린다.
    body = client.get("/api/stations", params={"q": "시청", "line": "2"}).json()

    assert body["limitations"] == [LINE_1_LIMITATION]


def test_like_wildcards_are_matched_literally(client):
    assert client.get("/api/stations", params={"q": "%"}).json()["stations"] == []
    assert client.get("/api/stations", params={"q": "_"}).json()["stations"] == []
    assert client.get("/api/stations", params={"q": "("}).json()["stations"][0]["station_name"] == "교대(법원, 검찰청)"


def test_partial_match_on_name_with_parentheses(client):
    body = client.get("/api/stations", params={"q": "검찰청"}).json()

    assert names(body) == ["교대(법원, 검찰청)"]
