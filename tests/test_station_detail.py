"""GET /api/stations/{station_id} 검증. 임시 SQLite DB 시드를 쓰며 개발 DB는 열지 않는다."""

import pytest

from app.services.stations import LINE_1_LIMITATION

DETAIL_KEYS = {"station_id", "station_name", "latitude", "longitude", "is_transfer", "lines", "limitations"}
LINE_KEYS = {"line_id", "line_name"}


def line_ids(body) -> list[str]:
    return [ln["line_id"] for ln in body["lines"]]


def test_general_station_detail(client):
    response = client.get("/api/stations/3")

    assert response.status_code == 200
    body = response.json()
    assert body["station_name"] == "강남"
    assert line_ids(body) == ["2"]
    assert body["is_transfer"] is False
    assert body["limitations"] == []  # 1호선이 아니므로 한계 문구 없음


def test_transfer_station_detail_lists_all_lines(client):
    body = client.get("/api/stations/2").json()  # 시청: 1·2호선

    assert body["station_name"] == "시청"
    assert line_ids(body) == ["1", "2"]
    assert body["is_transfer"] is True


def test_three_line_transfer_station(client):
    body = client.get("/api/stations/7").json()  # 종로3가: 1·3·5호선

    assert body["station_name"] == "종로3가"
    assert line_ids(body) == ["1", "3", "5"]
    assert body["is_transfer"] is True


def test_lines_are_sorted_by_line_number(client):
    body = client.get("/api/stations/10").json()  # 정렬시험: 8·2호선 순서로 저장됨

    assert line_ids(body) == ["2", "8"]


def test_line_1_station_gets_partial_coverage_limitation(client):
    # 서울역은 1호선·4호선이므로 계약 3.2절 예시와 D11 규칙에 따라 한계 문구가 있다.
    body = client.get("/api/stations/1").json()

    assert body["station_name"] == "서울역"
    assert body["limitations"] == [LINE_1_LIMITATION]


def test_no_limitation_for_stations_without_line_1(client):
    assert client.get("/api/stations/3").json()["limitations"] == []  # 강남 2호선
    assert client.get("/api/stations/5").json()["limitations"] == []  # 교대 2·3호선


def test_null_coordinates_are_returned_as_null(client):
    body = client.get("/api/stations/3").json()

    assert body["latitude"] is None
    assert body["longitude"] is None


def test_present_coordinates_are_returned(client, db):
    from app.models import Station

    station = db.get(Station, 3)
    station.latitude = 37.4979
    station.longitude = 127.0276
    db.commit()

    body = client.get("/api/stations/3").json()

    assert body["latitude"] == pytest.approx(37.4979)
    assert body["longitude"] == pytest.approx(127.0276)


def test_response_fields_match_contract(client):
    body = client.get("/api/stations/1").json()

    assert set(body) == DETAIL_KEYS
    assert set(body["lines"][0]) == LINE_KEYS
    assert isinstance(body["station_id"], int)
    assert isinstance(body["is_transfer"], bool)
    assert isinstance(body["limitations"], list)


def test_missing_station_returns_common_404(client):
    response = client.get("/api/stations/999")

    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"] == "요청한 리소스를 찾을 수 없습니다."


@pytest.mark.parametrize("station_id", ["0", "-1", "-999"])
def test_zero_and_negative_ids_return_404(client, station_id):
    response = client.get(f"/api/stations/{station_id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize("station_id", ["abc", "1.5", "12a", "1e3"])
def test_non_integer_id_returns_common_422(client, station_id):
    response = client.get(f"/api/stations/{station_id}")

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_search_api_still_works_after_detail_route_added(client):
    body = client.get("/api/stations", params={"q": "시청"}).json()

    assert [s["station_name"] for s in body["stations"]] == ["시청"]
    assert body["stations"][0]["lines"][0] == {"line_id": "1", "line_name": "1호선"}
    assert body["limitations"] == [LINE_1_LIMITATION]


def test_search_and_detail_agree_on_transfer_lines(client):
    search = client.get("/api/stations", params={"q": "종로3가"}).json()["stations"][0]
    detail = client.get(f"/api/stations/{search['station_id']}").json()

    assert search["lines"] == detail["lines"]
    assert search["station_id"] == detail["station_id"]
