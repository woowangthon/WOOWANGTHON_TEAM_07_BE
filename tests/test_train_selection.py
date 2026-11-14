"""시간표 기반 탑승 열차 선택(#38) 검증. 임시 SQLite만 쓴다.

2호선 강남(3) - 교대(5) - 서울대입구(6). 방향 1은 강남에서 서울대입구 쪽, 방향 2는 반대다. 2026-11-13은 금요일이다.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import StationEdge, StationLine, TrainSchedule
from app.routers import departure_time as departure_router
from app.services.routing import find_route
from app.services.status_messages import (
    DEP_HOLIDAY_UNKNOWN,
    DEP_NO_TIMETABLE,
    DEP_TRAIN_DIRECTION_INFERRED,
    DEP_TRAIN_NOT_FOUND,
    DEP_TRAIN_SELECTED,
    DEP_TRAIN_TRANSFER_UNSUPPORTED,
    DEP_TRAVEL_EXTERNAL,
    DEP_TRAVEL_NOT_USED,
)
from app.services.train_selection import infer_direction, select_train, single_line_leg
from app.timeutil import KST

H = 3600
FETCHED = "2026-11-14T03:00:00Z"
URL = "/api/departure-time"


def sl(db, station_id, line="2"):
    return db.scalar(select(StationLine.station_line_id).where(
        StationLine.station_id == station_id, StationLine.line_id == line))


def add_edge(db, a, b, edge_type="ADJACENT"):
    db.add(StationEdge(from_station_line_id=a, to_station_line_id=b, edge_type=edge_type, source="OA-101"))


def add_train(db, train_no, stops, *, day="WEEKDAY", direction=1, express=False):
    """stops: [(station_line_id, 도착 초 또는 None, 출발 초 또는 None)]"""
    for station_line_id, arrive, leave in stops:
        db.add(TrainSchedule(station_line_id=station_line_id, service_day_type=day, direction=direction,
                             train_no=train_no, arrival_time=arrive, departure_time=leave,
                             is_express=express, fetched_at=FETCHED))


def run(db, train_no, base, *, order=("g", "k", "s"), day="WEEKDAY", direction=1, express=False):
    """강남-교대-서울대입구를 base 초에 출발하는 열차. order로 진행 방향을 바꾼다."""
    ids = {"g": sl(db, 3), "k": sl(db, 5), "s": sl(db, 6)}
    stops = [
        (ids[order[0]], None, base),
        (ids[order[1]], base + 120, base + 150),
        (ids[order[2]], base + 270, None),
    ]
    add_train(db, train_no, stops, day=day, direction=direction, express=express)


@pytest.fixture
def line2(db):
    g, k, s = sl(db, 3), sl(db, 5), sl(db, 6)
    add_edge(db, g, k)
    add_edge(db, k, s)
    for i, base in enumerate((6 * H, 6 * H + 600, 6 * H + 1200)):
        run(db, f"200{i}", base, direction=1)
        run(db, f"300{i}", base, order=("s", "k", "g"), direction=2)
    db.commit()
    return db


def route(db, a, b):
    path = find_route(db, a, b, segment_minutes=2, transfer_minutes=5)
    assert path is not None
    return path


def arrival(day, hour, minute):
    return datetime(2026, 11, day, hour, minute, tzinfo=KST)


# ---------- 방향과 열차 선택 ----------


def test_selects_the_last_train_that_reaches_the_destination_in_time(line2):
    train = select_train(line2, route(line2, 3, 6), arrival(13, 6, 20), exit_walk_minutes=3)

    # 도착 06:20 - 출구 3분 = 06:17까지 하차. 06:10 출발 열차(하차 06:14:30)가 마지막이다.
    assert train is not None and train.train_no == "2001" and train.direction == 1
    assert train.boarding_at == arrival(13, 6, 10) and train.alighting_at == datetime(2026, 11, 13, 6, 14, 30, tzinfo=KST)


def test_opposite_route_uses_the_other_direction(line2):
    train = select_train(line2, route(line2, 6, 3), arrival(13, 6, 20), exit_walk_minutes=3)

    assert train is not None and train.train_no == "3001" and train.direction == 2


def test_arrival_exactly_at_alighting_plus_exit_is_accepted(line2):
    train = select_train(line2, route(line2, 3, 6), datetime(2026, 11, 13, 6, 17, 30, tzinfo=KST), exit_walk_minutes=3)

    assert train is not None and train.train_no == "2001"


def test_no_train_before_the_arrival_returns_none(line2):
    assert select_train(line2, route(line2, 3, 6), arrival(13, 6, 5), exit_walk_minutes=3) is None


def test_arrival_in_other_utc_offset_gives_the_same_train(line2):
    utc = datetime(2026, 11, 12, 21, 20, tzinfo=timezone.utc)  # 06:20 KST

    train = select_train(line2, route(line2, 3, 6), utc, exit_walk_minutes=3)

    assert train is not None and train.train_no == "2001"


def test_express_trains_are_ignored(line2):
    run(line2, "EX1", 6 * H + 700, direction=1, express=True)  # 일반 열차(06:10)보다 늦게 출발하지만 급행이다
    line2.commit()

    train = select_train(line2, route(line2, 3, 6), arrival(13, 6, 20), exit_walk_minutes=3)

    assert train is not None and train.train_no == "2001"


def test_direction_is_unknown_when_next_stop_has_no_rows(db):
    g, k, s = sl(db, 3), sl(db, 5), sl(db, 6)
    add_edge(db, g, k)
    add_edge(db, k, s)
    add_train(db, "2001", [(g, None, 6 * H), (s, 6 * H + 270, None)])  # 교대를 건너뛴 행만 있다
    db.commit()

    assert infer_direction(db, g, k, "WEEKDAY") is None
    assert select_train(db, route(db, 3, 6), arrival(13, 6, 20), exit_walk_minutes=3) is None


def test_direction_is_unknown_when_both_directions_match(line2):
    g = sl(line2, 3)
    run(line2, "9001", 6 * H + 60, direction=2)  # 방향 2도 같은 순서로 선다
    line2.commit()

    assert infer_direction(line2, g, sl(line2, 5), "WEEKDAY") is None
    assert select_train(line2, route(line2, 3, 6), arrival(13, 6, 20), exit_walk_minutes=3) is None


def test_long_gap_between_stops_does_not_count_as_adjacent(db):
    g, k, s = sl(db, 3), sl(db, 5), sl(db, 6)
    add_edge(db, g, k)
    add_edge(db, k, s)
    add_train(db, "2001", [(g, None, 6 * H), (k, 6 * H + 3600, 6 * H + 3630), (s, 6 * H + 3900, None)])
    db.commit()

    assert infer_direction(db, g, k, "WEEKDAY") is None


def test_service_day_types_are_chosen_by_weekday(db):
    g, k, s = sl(db, 3), sl(db, 5), sl(db, 6)
    add_edge(db, g, k)
    add_edge(db, k, s)
    run(db, "SAT1", 7 * H, day="SATURDAY")
    db.commit()
    path = route(db, 3, 6)

    assert select_train(db, path, arrival(14, 7, 30), exit_walk_minutes=3).train_no == "SAT1"  # 토요일
    assert select_train(db, path, arrival(13, 7, 30), exit_walk_minutes=3) is None  # 금요일은 평일 시간표
    assert select_train(db, path, arrival(15, 7, 30), exit_walk_minutes=3) is None  # 일요일은 HOLIDAY


def test_sunday_uses_holiday_timetable(db):
    add_edge(db, sl(db, 3), sl(db, 5))
    add_edge(db, sl(db, 5), sl(db, 6))
    run(db, "SUN1", 7 * H, day="HOLIDAY")
    db.commit()

    assert select_train(db, route(db, 3, 6), arrival(15, 7, 30), exit_walk_minutes=3).train_no == "SUN1"


def test_after_midnight_train_belongs_to_the_previous_service_day(line2):
    run(line2, "2900", 24 * H + 1800, direction=1)  # 금요일 서비스일 24:30
    line2.commit()

    train = select_train(line2, route(line2, 3, 6), arrival(14, 0, 50), exit_walk_minutes=3)  # 토요일 00:50

    assert train is not None and train.train_no == "2900"
    assert train.boarding_at == arrival(14, 0, 30)  # 실제 시각은 토요일 00:30
    assert train.alighting_at == datetime(2026, 11, 14, 0, 34, 30, tzinfo=KST)


def test_early_morning_train_of_the_same_day_wins_over_the_previous_service_day(line2):
    run(line2, "2900", 24 * H + 1800, direction=1)  # 토요일 00:30(금요일 서비스일)
    run(line2, "SAT0", 1900, day="SATURDAY", direction=1)  # 토요일 00:31:40
    line2.commit()

    train = select_train(line2, route(line2, 3, 6), arrival(14, 0, 50), exit_walk_minutes=3)

    assert train.train_no == "SAT0"


def test_transfer_route_is_not_supported(line2):
    k3 = sl(line2, 5, "3")
    j3 = sl(line2, 7, "3")
    add_edge(line2, sl(line2, 5), k3, "TRANSFER")
    add_edge(line2, k3, j3)
    line2.commit()
    path = route(line2, 3, 7)

    assert single_line_leg(path) is None
    assert select_train(line2, path, arrival(13, 7, 0), exit_walk_minutes=3) is None


def test_single_stop_route_has_no_leg(db):
    path = route(db, 3, 3)

    assert single_line_leg(path) is None


# ---------- API ----------


@pytest.fixture
def api(client, monkeypatch):
    monkeypatch.setattr(departure_router, "utc_now", lambda: datetime(2026, 11, 12, 20, 0, 0, tzinfo=timezone.utc))  # 05:00 KST
    return client


def post(client, **body):
    base = {"arrival_at": "2026-11-13T06:20:00+09:00", "travel_minutes": 30, "from_station_id": 3, "to_station_id": 6}
    base.update(body)
    return client.post(URL, json=base)


def test_api_uses_the_timetable_train(api, line2):
    response = post(api)

    assert response.status_code == 200
    body = response.json()
    assert body["timetable_used"] is True and body["estimated"] is True
    assert body["selected_train"] == {
        "train_no": "2001", "boarding_at": "2026-11-13T06:10:00+09:00",
        "alighting_at": "2026-11-13T06:14:30+09:00", "direction_inferred": True,
    }
    assert body["estimated_boarding_at"] == "2026-11-13T06:10:00+09:00"
    assert body["estimated_alighting_at"] == "2026-11-13T06:14:30+09:00"
    # 기본값: 도보 7 + 진입 3 + 여유 5 = 15분 전에 출발한다.
    assert body["recommended_departure_at"] == "2026-11-13T05:55:00+09:00"
    kinds = [step["kind"] for step in body["timeline"]]
    assert kinds == ["WALK_TO_STATION", "STATION_ENTRY", "BUFFER", "RIDE", "EXIT_WALK"]
    assert body["timeline"][0]["start_at"] == body["recommended_departure_at"]
    for a, b in zip(body["timeline"], body["timeline"][1:]):
        assert a["end_at"] == b["start_at"]
    assert body["timeline"][3]["minutes"] == 5  # 270초
    assert body["inputs"]["travel_minutes"] == 30  # 입력 그대로이며 계산에는 쓰지 않는다
    for message in (DEP_TRAIN_SELECTED, DEP_TRAIN_DIRECTION_INFERRED, DEP_TRAVEL_NOT_USED, DEP_HOLIDAY_UNKNOWN):
        assert message in body["limitations"]
    assert DEP_NO_TIMETABLE not in body["limitations"] and DEP_TRAVEL_EXTERNAL not in body["limitations"]


def test_api_without_station_ids_is_unchanged(api, line2):
    body = api.post(URL, json={"arrival_at": "2026-11-13T09:00:00+09:00", "travel_minutes": 30}).json()

    assert body["timetable_used"] is False and body["selected_train"] is None
    assert body["recommended_departure_at"] == "2026-11-13T08:12:00+09:00"  # 7+3+5+30+3 = 48분
    assert DEP_NO_TIMETABLE in body["limitations"] and DEP_TRAIN_NOT_FOUND not in body["limitations"]


def test_api_falls_back_when_no_train_fits(api, line2):
    body = post(api, arrival_at="2026-11-13T06:05:00+09:00").json()

    assert body["timetable_used"] is False and body["selected_train"] is None
    assert DEP_NO_TIMETABLE in body["limitations"] and DEP_TRAIN_NOT_FOUND in body["limitations"]
    assert body["recommended_departure_at"] == "2026-11-13T05:17:00+09:00"  # 48분 전, 기존 추정과 같다


def test_api_falls_back_when_the_timetable_is_empty(api, db):
    add_edge(db, sl(db, 3), sl(db, 5))
    add_edge(db, sl(db, 5), sl(db, 6))
    db.commit()

    body = post(api).json()

    assert body["timetable_used"] is False and body["selected_train"] is None
    assert DEP_TRAIN_NOT_FOUND in body["limitations"]


def test_api_transfer_route_falls_back_with_a_note(api, line2):
    add_edge(line2, sl(line2, 5), sl(line2, 5, "3"), "TRANSFER")
    add_edge(line2, sl(line2, 5, "3"), sl(line2, 7, "3"))
    line2.commit()

    body = post(api, to_station_id=7).json()

    assert body["timetable_used"] is False and body["selected_train"] is None
    assert DEP_TRAIN_TRANSFER_UNSUPPORTED in body["limitations"]


def test_api_no_route_keeps_the_estimate(api, db):
    body = post(api).json()

    assert body["timetable_used"] is False and body["selected_train"] is None
    assert DEP_TRAIN_NOT_FOUND not in body["limitations"]


def test_api_already_past_is_flagged_for_the_selected_train(api, line2, monkeypatch):
    monkeypatch.setattr(departure_router, "utc_now", lambda: datetime(2026, 11, 12, 21, 0, 0, tzinfo=timezone.utc))  # 06:00 KST

    body = post(api).json()

    assert body["timetable_used"] is True and body["already_past"] is True


# ---------- 순수 함수 ----------


def test_recommend_departure_with_a_train_rejects_half_or_inconsistent_times():
    from app.services.departure_time import recommend_departure

    arrival_at = datetime(2026, 11, 13, 6, 20, tzinfo=KST)
    board = datetime(2026, 11, 13, 6, 10, tzinfo=KST)
    alight = datetime(2026, 11, 13, 6, 14, tzinfo=KST)
    kwargs = {"travel_minutes": 30, "buffer_minutes": 5}

    with pytest.raises(ValueError):
        recommend_departure(arrival_at, boarding_at=board, **kwargs)
    with pytest.raises(ValueError):
        recommend_departure(arrival_at, boarding_at=alight, alighting_at=board, **kwargs)
    with pytest.raises(ValueError):  # 하차 06:19 + 출구 3분이 도착 06:20을 넘는다
        recommend_departure(arrival_at, boarding_at=board, alighting_at=datetime(2026, 11, 13, 6, 19, tzinfo=KST), **kwargs)

    rec = recommend_departure(arrival_at, boarding_at=board, alighting_at=alight, **kwargs)
    assert rec.timetable_used is True and rec.estimated is True
    assert rec.recommended_departure_at == datetime(2026, 11, 13, 5, 55, tzinfo=KST)
