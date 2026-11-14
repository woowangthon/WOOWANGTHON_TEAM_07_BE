"""상태 판정 순수 함수 검증 (계약 6.1절, 6.2절). 외부 자원 없이 입력만 쓴다."""

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services import status_judgment as sj
from app.services.status_judgment import (
    ElevatorRef,
    StatusRow,
    judge_station_without_elevators,
    judge_stations,
)
from app.services.status_messages import LINE_1_PARTIAL, NO_CONFIRMED, NO_ELEVATORS, PARTIAL_UNKNOWN

NOW = datetime(2030, 1, 1, 3, 0, 0, tzinfo=timezone.utc)
TTL = 30
MODULE = Path(sj.__file__)


def at(minutes_before_now: int) -> str:
    """NOW보다 몇 분 전인지로 fetched_at 문자열을 만든다."""
    return (NOW - timedelta(minutes=minutes_before_now)).strftime("%Y-%m-%dT%H:%M:%SZ")


def one(judgments, station_id=10):
    return next(j for j in judgments if j.station_id == station_id)


def elevator(judgments, elevator_id, station_id=10):
    return next(e for e in one(judgments, station_id).elevators if e.elevator_id == elevator_id)


def judge(elevators, statuses, now=NOW, ttl=TTL):
    return judge_stations(elevators, statuses, now=now, ttl_minutes=ttl)


# ---------- 설비 판정: NO_DATA, STALE, 가동중, 가동 안됨 ----------


def test_missing_status_row_is_no_data():
    e = elevator(judge([ElevatorRef(1, 10)], []), 1)

    assert (e.status, e.data_fresh, e.unknown_reason, e.fetched_at) == (None, False, "NO_DATA", None)


def test_stale_row_is_reported_as_stale_without_its_status():
    e = elevator(judge([ElevatorRef(1, 10)], [StatusRow(1, "가동중", at(31))]), 1)

    assert (e.status, e.data_fresh, e.unknown_reason) == (None, False, "STALE")
    assert e.fetched_at == at(31)  # 저장된 시각은 보여 주되 상태는 현재 값처럼 쓰지 않는다


def test_fresh_running_elevator_keeps_its_status():
    e = elevator(judge([ElevatorRef(1, 10)], [StatusRow(1, "가동중", at(5))]), 1)

    assert (e.status, e.data_fresh, e.unknown_reason) == ("가동중", True, None)


def test_fresh_stopped_elevator_keeps_its_status():
    e = elevator(judge([ElevatorRef(1, 10)], [StatusRow(1, "가동 안됨", at(5))]), 1)

    assert (e.status, e.data_fresh, e.unknown_reason) == ("가동 안됨", True, None)


def test_observed_at_is_always_none():
    e = elevator(judge([ElevatorRef(1, 10)], [StatusRow(1, "가동중", at(5))]), 1)

    assert e.observed_at is None


# ---------- TTL 경계 ----------


def test_zero_elapsed_is_fresh():
    e = elevator(judge([ElevatorRef(1, 10)], [StatusRow(1, "가동중", at(0))]), 1)

    assert e.data_fresh is True


def test_exactly_the_ttl_is_still_fresh():
    e = elevator(judge([ElevatorRef(1, 10)], [StatusRow(1, "가동중", at(TTL))]), 1)

    assert e.data_fresh is True and e.status == "가동중"


def test_one_second_past_the_ttl_is_stale():
    row = StatusRow(1, "가동중", (NOW - timedelta(minutes=TTL) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))

    e = elevator(judge([ElevatorRef(1, 10)], [row]), 1)

    assert e.unknown_reason == "STALE" and e.status is None


def test_one_minute_past_the_ttl_is_stale():
    e = elevator(judge([ElevatorRef(1, 10)], [StatusRow(1, "가동중", at(TTL + 1))]), 1)

    assert e.unknown_reason == "STALE"


@pytest.mark.parametrize("ttl", [1, 30, 1440])
def test_ttl_bounds_are_accepted(ttl):
    e = elevator(judge([ElevatorRef(1, 10)], [StatusRow(1, "가동중", at(0))], ttl=ttl), 1)

    assert e.data_fresh is True


# ---------- 시각과 값 검증 ----------


def test_future_fetched_at_is_rejected_with_no_tolerance():
    with pytest.raises(ValueError, match="미래"):
        judge([ElevatorRef(1, 10)], [StatusRow(1, "가동중", at(-1))])


def test_fetched_at_one_second_in_the_future_is_rejected():
    future = (NOW + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    with pytest.raises(ValueError, match="미래"):
        judge([ElevatorRef(1, 10)], [StatusRow(1, "가동중", future)])


def test_fetched_at_equal_to_now_is_fresh_not_future():
    e = elevator(judge([ElevatorRef(1, 10)], [StatusRow(1, "가동중", at(0))]), 1)

    assert e.data_fresh is True


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "2030-01-01",
        "2030-01-01T03:00:00",  # 타임존 없음
        "2030-01-01T03:00:00+00:00",  # 저장 형식(Z)이 아님
        "2030-01-01T12:00:00+09:00",
        "2030-01-01 03:00:00Z",
        "2030-13-01T03:00:00Z",  # 잘못된 월
        "2030-02-30T03:00:00Z",  # 없는 날짜
        "2030-01-01T03:00:00.5Z",
        None,
        1893466800,
        b"2030-01-01T03:00:00Z",
    ],
)
def test_malformed_fetched_at_is_rejected(bad):
    with pytest.raises(ValueError):
        judge([ElevatorRef(1, 10)], [StatusRow(1, "가동중", bad)])


@pytest.mark.parametrize("bad_status", ["고장", "보수중", "사용가능", "", "가동중 ", None, "UNKNOWN"])
def test_unexpected_status_value_is_rejected(bad_status):
    with pytest.raises(ValueError, match="상태"):
        judge([ElevatorRef(1, 10)], [StatusRow(1, bad_status, at(5))])


def test_unexpected_status_is_rejected_even_when_stale():
    with pytest.raises(ValueError):
        judge([ElevatorRef(1, 10)], [StatusRow(1, "고장", at(999))])


@pytest.mark.parametrize("now", [datetime(2030, 1, 1, 3, 0), datetime(2030, 1, 1, 12, tzinfo=timezone(timedelta(hours=9))), "2030-01-01T03:00:00Z"])
def test_now_must_be_utc_aware_datetime(now):
    with pytest.raises(ValueError):
        judge([ElevatorRef(1, 10)], [], now=now)


@pytest.mark.parametrize("ttl", [0, -1, 1441, 30.0, "30", True, None])
def test_ttl_outside_integer_range_is_rejected(ttl):
    with pytest.raises(ValueError):
        judge([ElevatorRef(1, 10)], [], ttl=ttl)


def test_duplicate_elevator_ids_are_rejected():
    with pytest.raises(ValueError, match="중복"):
        judge([ElevatorRef(1, 10), ElevatorRef(1, 10)], [])


def test_duplicate_status_rows_are_rejected():
    with pytest.raises(ValueError, match="중복"):
        judge([ElevatorRef(1, 10)], [StatusRow(1, "가동중", at(1)), StatusRow(1, "가동 안됨", at(2))])


def test_wrong_item_types_are_rejected():
    with pytest.raises(ValueError):
        judge([(1, 10)], [])
    with pytest.raises(ValueError):
        judge([ElevatorRef(1, 10)], [("1", "가동중", at(1))])


def test_status_row_for_an_unknown_elevator_is_ignored():
    result = judge([ElevatorRef(1, 10)], [StatusRow(99, "가동중", at(1))])

    assert one(result).unknown_elevator_count == 1  # 설비 1은 상태 행이 없다


# ---------- 역 판정 ----------


def test_all_running_is_available_and_fresh():
    r = one(judge([ElevatorRef(1, 10), ElevatorRef(2, 10)], [StatusRow(1, "가동중", at(1)), StatusRow(2, "가동중", at(2))]))

    assert (r.status, r.data_fresh, r.unknown_elevator_count, r.limitations) == ("AVAILABLE", True, 0, ())
    assert (r.total_elevator_count) == 2


def test_all_stopped_is_unavailable_and_fresh():
    r = one(judge([ElevatorRef(1, 10), ElevatorRef(2, 10)], [StatusRow(1, "가동 안됨", at(1)), StatusRow(2, "가동 안됨", at(2))]))

    assert (r.status, r.data_fresh, r.limitations) == ("UNAVAILABLE", True, ())


def test_mixed_confirmed_is_partial_and_fresh():
    r = one(judge([ElevatorRef(1, 10), ElevatorRef(2, 10)], [StatusRow(1, "가동중", at(1)), StatusRow(2, "가동 안됨", at(2))]))

    assert (r.status, r.data_fresh, r.unknown_elevator_count, r.limitations) == ("PARTIAL", True, 0, ())


def test_all_unknown_gives_null_status_and_counts_every_elevator():
    r = one(judge([ElevatorRef(1, 10), ElevatorRef(2, 10)], []))

    assert (r.status, r.data_fresh, r.unknown_elevator_count, r.total_elevator_count) == (None, False, 2, 2)
    assert r.limitations == (NO_CONFIRMED,)


def test_all_stale_gives_null_status():
    r = one(judge([ElevatorRef(1, 10), ElevatorRef(2, 10)], [StatusRow(1, "가동중", at(60)), StatusRow(2, "가동 안됨", at(90))]))

    assert (r.status, r.unknown_elevator_count, r.data_fresh) == (None, 2, False)
    assert r.limitations == (NO_CONFIRMED,)


def test_partial_unknown_keeps_the_status_and_reports_it():
    r = one(judge([ElevatorRef(1, 10), ElevatorRef(2, 10), ElevatorRef(3, 10)], [StatusRow(1, "가동중", at(1)), StatusRow(2, "가동중", at(2))]))

    assert (r.status, r.data_fresh, r.unknown_elevator_count) == ("AVAILABLE", False, 1)
    assert r.limitations == (PARTIAL_UNKNOWN,)


def test_partial_unknown_with_mixed_confirmed_is_partial_not_fresh():
    r = one(judge([ElevatorRef(1, 10), ElevatorRef(2, 10), ElevatorRef(3, 10)], [StatusRow(1, "가동중", at(1)), StatusRow(2, "가동 안됨", at(2))]))

    assert (r.status, r.data_fresh, r.unknown_elevator_count) == ("PARTIAL", False, 1)


def test_stale_counts_as_unknown_for_the_station():
    result = one(judge([ElevatorRef(1, 10), ElevatorRef(2, 10)], [StatusRow(1, "가동중", at(1)), StatusRow(2, "가동 안됨", at(45))]))

    assert result.unknown_elevator_count == 1  # 2번은 STALE이지만 미확인으로 센다
    assert result.status == "AVAILABLE"  # 확인된 설비(1번)만으로 판정한다
    assert result.data_fresh is False and result.limitations == (PARTIAL_UNKNOWN,)


def test_one_stale_and_one_no_data_both_count_as_unknown():
    r = one(judge([ElevatorRef(1, 10), ElevatorRef(2, 10), ElevatorRef(3, 10)], [StatusRow(1, "가동 안됨", at(1)), StatusRow(2, "가동중", at(99))]))

    assert r.unknown_elevator_count == 2 and r.status == "UNAVAILABLE"


def test_station_without_elevators_is_null_with_the_standard_message():
    r = judge_station_without_elevators(77)

    assert (r.station_id, r.status, r.data_fresh, r.total_elevator_count, r.unknown_elevator_count) == (77, None, False, 0, 0)
    assert r.limitations == (NO_ELEVATORS,) and r.elevators == ()


def test_station_with_no_elevators_in_input_is_not_returned():
    assert [j.station_id for j in judge([ElevatorRef(1, 10)], [StatusRow(1, "가동중", at(1))])] == [10]


def test_stations_are_returned_in_station_id_order():
    result = judge(
        [ElevatorRef(1, 30), ElevatorRef(2, 10), ElevatorRef(3, 20)],
        [StatusRow(1, "가동중", at(1)), StatusRow(2, "가동중", at(1)), StatusRow(3, "가동중", at(1))],
    )

    assert [j.station_id for j in result] == [10, 20, 30]


def test_elevators_inside_a_station_are_in_elevator_id_order():
    result = judge([ElevatorRef(5, 10), ElevatorRef(2, 10), ElevatorRef(9, 10)], [])

    assert [e.elevator_id for e in one(result).elevators] == [2, 5, 9]


def test_limitations_only_use_contract_standard_messages():
    allowed = {PARTIAL_UNKNOWN, NO_CONFIRMED, NO_ELEVATORS}
    cases = [
        judge([ElevatorRef(1, 10), ElevatorRef(2, 10)], [StatusRow(1, "가동중", at(1))]),
        judge([ElevatorRef(1, 10)], []),
        judge([ElevatorRef(1, 10)], [StatusRow(1, "가동중", at(1))]),
    ]
    for result in cases:
        for station in result:
            assert set(station.limitations) <= allowed
    assert LINE_1_PARTIAL not in sum((list(s.limitations) for c in cases for s in c), [])  # 노선 문구는 여기서 붙이지 않는다


def test_results_are_immutable_dataclasses():
    r = one(judge([ElevatorRef(1, 10)], [StatusRow(1, "가동중", at(1))]))

    with pytest.raises(Exception):
        r.status = "가동 안됨"  # frozen


# ---------- 결정성과 순수성 ----------


def test_same_input_gives_the_same_result():
    elevators = [ElevatorRef(1, 10), ElevatorRef(2, 10), ElevatorRef(3, 20)]
    statuses = [StatusRow(1, "가동중", at(1)), StatusRow(3, "가동 안됨", at(50))]

    assert judge(elevators, statuses) == judge(elevators, statuses)


def test_input_order_does_not_change_the_result():
    elevators = [ElevatorRef(1, 10), ElevatorRef(2, 10)]
    statuses = [StatusRow(1, "가동중", at(1)), StatusRow(2, "가동 안됨", at(2))]

    assert judge(elevators, statuses) == judge(list(reversed(elevators)), list(reversed(statuses)))


def test_now_is_injected_not_read_from_the_clock():
    elevators = [ElevatorRef(1, 10)]
    statuses = [StatusRow(1, "가동중", "2030-01-01T03:00:00Z")]

    early = judge(elevators, statuses, now=datetime(2030, 1, 1, 3, 10, tzinfo=timezone.utc))
    late = judge(elevators, statuses, now=datetime(2030, 1, 1, 4, 0, tzinfo=timezone.utc))

    assert elevator(early, 1).data_fresh is True
    assert elevator(late, 1).unknown_reason == "STALE"


def test_module_has_no_database_network_clock_or_file_access():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = {
        "sqlalchemy", "app.database", "app.config", "app.models", "httpx", "socket", "requests",
        "urllib", "http", "os", "pathlib", "time", "app.timeutil", "app.clients",
    }
    assert not {m for m in imported for f in forbidden if m == f or m.startswith(f + ".")}
    banned_calls = {"now", "utcnow", "today", "open", "connect", "get"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"now", "utcnow", "today"}:
            pytest.fail("시스템 시계를 읽습니다")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            pytest.fail("파일을 엽니다")
    assert "datetime.now" not in MODULE.read_text(encoding="utf-8")
