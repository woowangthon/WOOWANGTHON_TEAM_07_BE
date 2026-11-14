"""POST /api/departure-time 검증. DB를 쓰지 않는 API라 세션 의존성이 호출되면 실패하도록 막아 둔다."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import config
from app.database import get_session
from app.main import create_app
from app.services.status_messages import (
    DEP_ALREADY_PAST,
    DEP_ELEVATOR_UNCONFIRMED,
    DEP_NO_TIMETABLE,
    DEP_SEGMENT_ESTIMATES,
    DEP_TRAVEL_EXTERNAL,
)

URL = "/api/departure-time"
UTC = timezone.utc
FIXED_NOW = datetime(2026, 11, 13, 0, 0, 0, 123456, tzinfo=UTC)  # 마이크로초가 있어도 동작해야 한다


class _GuardSession:
    """세션을 받아도 쓰는 순간 실패한다. 역 id가 없는 요청은 DB를 쓰지 않아야 한다."""

    def __getattr__(self, name):
        raise AssertionError("역 id가 없는 요청은 DB를 쓰지 않는다")


def forbid_db():
    yield _GuardSession()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.routers.departure_time.utc_now", lambda: FIXED_NOW)
    app = create_app()
    app.dependency_overrides[get_session] = forbid_db
    return TestClient(app)


def post(client, **body):
    base = {"arrival_at": "2026-11-13T09:00:00+09:00", "travel_minutes": 30}
    base.update(body)
    return client.post(URL, json=base)


# ---------- 정상 응답 ----------


def test_response_example_with_defaults(client):
    response = post(client, buffer_minutes=10)

    assert response.status_code == 200
    body = response.json()
    assert body["recommended_departure_at"] == "2026-11-13T08:07:00+09:00"
    assert body["estimated_boarding_at"] == "2026-11-13T08:27:00+09:00"  # 08:07 + 도보 7 + 진입 3 + 여유 10
    assert body["estimated_alighting_at"] == "2026-11-13T08:57:00+09:00"  # 탑승 + 이동 30
    assert body["arrival_at"] == "2026-11-13T09:00:00+09:00"
    assert body["total_minutes"] == 53 and body["date_changed"] is False
    assert body["inputs"] == {"travel_minutes": 30, "buffer_minutes": 10, "walk_to_station_minutes": 7,
                              "station_entry_minutes": 3, "exit_walk_minutes": 3}
    assert body["defaults_applied"] == ["walk_to_station_minutes", "station_entry_minutes", "exit_walk_minutes"]


def test_response_fields_match_contract(client):
    body = post(client).json()

    assert set(body) == {
        "recommended_departure_at", "estimated_boarding_at", "estimated_alighting_at", "arrival_at",
        "total_minutes", "date_changed", "already_past", "estimated", "timetable_used", "inputs",
        "defaults_applied", "timeline", "elevator_warning", "limitations",
    }
    assert set(body["timeline"][0]) == {"kind", "label", "start_at", "end_at", "minutes"}
    assert set(body["elevator_warning"]) == {"status", "items", "message"}
    json.dumps(body, ensure_ascii=False)


def test_buffer_defaults_to_five_and_segment_overrides_apply(client):
    body = post(client, walk_to_station_minutes=15, station_entry_minutes=0, exit_walk_minutes=5).json()

    assert body["inputs"]["buffer_minutes"] == 5
    assert body["total_minutes"] == 15 + 0 + 5 + 30 + 5
    assert body["defaults_applied"] == ["buffer_minutes"]


def test_explicit_zero_buffer_is_not_reported_as_default(client):
    body = post(client, buffer_minutes=0, walk_to_station_minutes=15, station_entry_minutes=0, exit_walk_minutes=5).json()

    assert body["inputs"]["buffer_minutes"] == 0
    assert body["defaults_applied"] == []


def test_omitted_segments_use_settings_not_hardcoded_values(client, monkeypatch):
    monkeypatch.setattr(config.settings, "walk_to_station_minutes", 20)
    monkeypatch.setattr(config.settings, "exit_walk_minutes", 1)

    body = post(client).json()

    assert body["inputs"]["walk_to_station_minutes"] == 20 and body["inputs"]["exit_walk_minutes"] == 1
    assert body["inputs"]["station_entry_minutes"] == 3
    assert body["defaults_applied"] == ["buffer_minutes", "walk_to_station_minutes", "station_entry_minutes", "exit_walk_minutes"]


def test_null_segment_means_use_the_default(client):
    body = post(client, walk_to_station_minutes=None, exit_walk_minutes=9).json()

    assert body["inputs"]["walk_to_station_minutes"] == 7 and body["inputs"]["exit_walk_minutes"] == 9
    assert body["defaults_applied"] == ["buffer_minutes", "walk_to_station_minutes", "station_entry_minutes"]


def test_timeline_is_contiguous_and_ends_at_arrival(client):
    body = post(client, buffer_minutes=10).json()

    steps = body["timeline"]
    assert [s["kind"] for s in steps] == ["WALK_TO_STATION", "STATION_ENTRY", "BUFFER", "RIDE", "EXIT_WALK"]
    assert steps[0]["start_at"] == body["recommended_departure_at"]
    for before, after in zip(steps, steps[1:]):
        assert before["end_at"] == after["start_at"]
    assert steps[-1]["end_at"] == body["arrival_at"]
    ride = steps[3]
    assert (ride["start_at"], ride["end_at"]) == (body["estimated_boarding_at"], body["estimated_alighting_at"])


# ---------- 추정·시간표 없음·표현 ----------


def test_always_estimated_without_timetable_and_states_limitations(client):
    body = post(client).json()

    assert body["estimated"] is True and body["timetable_used"] is False
    for message in (DEP_NO_TIMETABLE, DEP_TRAVEL_EXTERNAL, DEP_SEGMENT_ESTIMATES):
        assert message in body["limitations"]


def test_times_are_not_presented_as_a_confirmed_train(client):
    text = post(client).text

    assert "estimated_boarding_at" in text and "estimated_alighting_at" in text
    for forbidden in ("train_no", "열차 번호", "탑승 확정", "도착 확정", "confirmed_boarding"):
        assert forbidden not in text
    ride = next(s for s in post(client).json()["timeline"] if s["kind"] == "RIDE")
    assert "외부 입력 예상" in ride["label"]


def test_elevator_warning_is_unconfirmed_and_never_invented(client):
    body = post(client).json()

    assert body["elevator_warning"] == {"status": "UNCONFIRMED", "items": [], "message": DEP_ELEVATOR_UNCONFIRMED}
    assert DEP_ELEVATOR_UNCONFIRMED in body["limitations"]


# ---------- KST/UTC, 자정 이후 ----------


@pytest.mark.parametrize(
    "arrival",
    ["2026-11-13T09:00:00+09:00", "2026-11-13T00:00:00Z", "2026-11-13T00:00:00+00:00", "2026-11-12T19:00:00-05:00"],
)
def test_same_instant_in_any_offset_gives_the_same_kst_response(client, arrival):
    body = post(client, arrival_at=arrival, buffer_minutes=10).json()

    assert body["recommended_departure_at"] == "2026-11-13T08:07:00+09:00"
    assert body["arrival_at"] == "2026-11-13T09:00:00+09:00"
    assert all(s["start_at"].endswith("+09:00") and s["end_at"].endswith("+09:00") for s in body["timeline"])


def test_arrival_after_midnight_departs_the_previous_day(client):
    body = post(client, arrival_at="2026-11-14T00:10:00+09:00", buffer_minutes=10).json()

    assert body["recommended_departure_at"] == "2026-11-13T23:17:00+09:00"
    assert body["date_changed"] is True
    assert body["estimated_boarding_at"] == "2026-11-13T23:37:00+09:00"
    assert body["estimated_alighting_at"] == "2026-11-14T00:07:00+09:00"


def test_utc_arrival_that_is_midnight_in_kst(client):
    body = post(client, arrival_at="2026-11-13T15:00:00Z", travel_minutes=1, buffer_minutes=0,
                walk_to_station_minutes=0, station_entry_minutes=0, exit_walk_minutes=0).json()

    assert body["arrival_at"] == "2026-11-14T00:00:00+09:00"
    assert body["recommended_departure_at"] == "2026-11-13T23:59:00+09:00"
    assert body["date_changed"] is True


def test_year_boundary(client):
    body = post(client, arrival_at="2027-01-01T00:05:00+09:00").json()

    assert body["recommended_departure_at"] == "2026-12-31T23:17:00+09:00"  # 기본 대기 5분 포함


# ---------- 이미 지난 출발 시각 ----------


def test_departure_already_past_is_flagged_with_the_server_clock(client):
    # 고정 시각은 2026-11-13T09:00:00 KST(= 00:00:00Z). 08:07 출발은 이미 지났다.
    past = post(client, buffer_minutes=10).json()
    assert past["already_past"] is True and DEP_ALREADY_PAST in past["limitations"]

    future = post(client, arrival_at="2026-11-13T12:00:00+09:00").json()
    assert future["already_past"] is False and DEP_ALREADY_PAST not in future["limitations"]


def test_already_past_boundary_equal_to_now_is_not_past(client):
    # 출발 = 도착 - 48분(기본 대기 5분 포함) = 09:00:00 KST(= 고정 now 00:00:00Z)
    body = post(client, arrival_at="2026-11-13T09:48:00+09:00").json()

    assert body["recommended_departure_at"] == "2026-11-13T09:00:00+09:00"
    assert body["already_past"] is False


def test_one_second_before_now_is_past(client):
    body = post(client, arrival_at="2026-11-13T09:42:59+09:00").json()

    assert body["already_past"] is True


def test_works_with_the_real_clock_microseconds(monkeypatch):
    app = create_app()
    app.dependency_overrides[get_session] = forbid_db

    response = TestClient(app).post(URL, json={"arrival_at": "2099-01-01T09:00:00+09:00", "travel_minutes": 30})

    assert response.status_code == 200 and response.json()["already_past"] is False


# ---------- 잘못된 입력 ----------


def assert_validation_error(response):
    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "arrival",
    ["2026-11-13T09:00:00", "2026-11-13", "2026-11-13 09:00:00+09:00", "2026-13-40T09:00:00+09:00",
     "2026-11-13T25:00:00+09:00", "2026-11-13T09:00:00.500+09:00", "", "내일 아침", 1_700_000_000, None, True],
)
def test_invalid_arrival_is_rejected(client, arrival):
    assert_validation_error(post(client, arrival_at=arrival))


@pytest.mark.parametrize("arrival", ["0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z"])
def test_arrival_outside_representable_range_is_rejected_not_a_500(client, arrival):
    assert_validation_error(post(client, arrival_at=arrival))


@pytest.mark.parametrize("field", ["travel_minutes", "buffer_minutes", "walk_to_station_minutes", "station_entry_minutes", "exit_walk_minutes"])
@pytest.mark.parametrize("bad", ["30", 30.5, 30.0, True, [30], {"m": 30}, 10**30])
def test_non_integer_or_absurd_numbers_are_rejected(client, field, bad):
    assert_validation_error(post(client, **{field: bad}))


@pytest.mark.parametrize(
    ("field", "low", "high"),
    [("travel_minutes", 1, 720), ("buffer_minutes", 0, 240), ("walk_to_station_minutes", 0, 120),
     ("station_entry_minutes", 0, 120), ("exit_walk_minutes", 0, 120)],
)
def test_range_boundaries(client, field, low, high):
    for ok in (low, high):
        assert post(client, **{field: ok}).status_code == 200
    for bad in (low - 1, high + 1):
        assert_validation_error(post(client, **{field: bad}))


def test_required_fields_and_null_for_required_numbers(client):
    assert_validation_error(client.post(URL, json={"travel_minutes": 30}))
    assert_validation_error(client.post(URL, json={"arrival_at": "2026-11-13T09:00:00+09:00"}))
    assert_validation_error(post(client, travel_minutes=None))
    assert_validation_error(post(client, buffer_minutes=None))


def test_unknown_fields_are_rejected(client):
    assert_validation_error(post(client, now="2026-11-13T00:00:00Z"))  # 현재 시각은 요청으로 받지 않는다
    assert_validation_error(post(client, timetable=True))
    assert_validation_error(post(client, station_id=1))


@pytest.mark.parametrize("content", [b"", b"not json", b"[]", b"null", b'"text"'])
def test_malformed_bodies_are_rejected(client, content):
    assert_validation_error(client.post(URL, content=content, headers={"content-type": "application/json"}))


def test_other_methods_are_not_allowed(client):
    response = client.get(URL)

    assert response.status_code == 405 and response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


# ---------- 저장 없음·재현성 ----------


def test_no_database_access_and_repeated_calls_are_identical(client):
    first = post(client, buffer_minutes=10).json()
    second = post(client, buffer_minutes=10).json()

    assert first == second  # forbid_db 의존성이 있어도 호출되지 않는다
