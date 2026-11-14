"""출발 권장 시각 계산 검증. 순수 함수라 DB·네트워크 없이 시각을 직접 넣어 확인한다."""

import ast
import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import Settings
from app.departure_defaults import (
    EXIT_WALK_DEFAULT,
    STATION_ENTRY_DEFAULT,
    WALK_TO_STATION_DEFAULT,
)
from app.services import departure_time as dt
from app.services.departure_time import parse_arrival_iso, recommend_departure, to_kst_dict
from app.services.status_messages import (
    DEP_ALREADY_PAST,
    DEP_NO_TIMETABLE,
    DEP_SEGMENT_ESTIMATES,
    DEP_TRAVEL_EXTERNAL,
)

KST = timezone(timedelta(hours=9))
UTC = timezone.utc


def kst(*args):
    return datetime(*args, tzinfo=KST)


ARRIVAL = kst(2026, 11, 13, 9, 0, 0)


def rec(arrival=ARRIVAL, travel=30, buffer=10, **kw):
    return recommend_departure(arrival, travel_minutes=travel, buffer_minutes=buffer, **kw)


# ---------- 기본 계산 ----------


def test_departure_is_arrival_minus_all_segments_with_default_estimates():
    r = rec()  # 도보 7 + 진입 3 + 여유 10 + 이동 30 + 출구 3 = 53분

    assert r.total_minutes == 53
    assert r.recommended_departure_at == kst(2026, 11, 13, 8, 7, 0)
    assert r.arrival_at == ARRIVAL


def test_timeline_is_contiguous_and_ends_exactly_at_arrival():
    r = rec()

    assert [s.kind for s in r.timeline] == ["WALK_TO_STATION", "STATION_ENTRY", "BUFFER", "RIDE", "EXIT_WALK"]
    assert [s.minutes for s in r.timeline] == [7, 3, 10, 30, 3]
    assert r.timeline[0].start_at == r.recommended_departure_at
    for before, after in zip(r.timeline, r.timeline[1:]):
        assert before.end_at == after.start_at
    assert r.timeline[-1].end_at == r.arrival_at
    assert sum(s.minutes for s in r.timeline) == r.total_minutes


def test_explicit_segment_values_override_defaults():
    r = rec(walk_to_station_minutes=15, station_entry_minutes=0, exit_walk_minutes=5)

    assert r.total_minutes == 15 + 0 + 10 + 30 + 5
    assert r.inputs.walk_to_station_minutes == 15 and r.inputs.station_entry_minutes == 0


def test_defaults_match_the_settings_defaults():
    s = Settings(_env_file=None)

    assert (WALK_TO_STATION_DEFAULT, STATION_ENTRY_DEFAULT, EXIT_WALK_DEFAULT) == (7, 3, 3)
    assert (s.walk_to_station_minutes, s.station_entry_minutes, s.exit_walk_minutes) == (7, 3, 3)


# ---------- KST/UTC ----------


@pytest.mark.parametrize(
    "arrival",
    [
        kst(2026, 11, 13, 9, 0, 0),
        datetime(2026, 11, 13, 0, 0, 0, tzinfo=UTC),
        datetime(2026, 11, 12, 19, 0, 0, tzinfo=timezone(timedelta(hours=-5))),
        datetime(2026, 11, 13, 5, 30, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))),
    ],
)
def test_same_instant_in_any_offset_gives_the_same_result(arrival):
    r = rec(arrival)

    assert r.recommended_departure_at == kst(2026, 11, 13, 8, 7, 0)
    assert r.arrival_at.utcoffset() == timedelta(0)  # 내부는 UTC
    assert to_kst_dict(r)["recommended_departure_at"] == "2026-11-13T08:07:00+09:00"


def test_utc_arrival_that_is_after_midnight_in_kst():
    r = rec(datetime(2026, 11, 13, 15, 10, 0, tzinfo=UTC))  # KST 11-14 00:10

    out = to_kst_dict(r)
    assert out["arrival_at"] == "2026-11-14T00:10:00+09:00"
    assert out["recommended_departure_at"] == "2026-11-13T23:17:00+09:00"
    assert out["date_changed"] is True


# ---------- 날짜 변경, 자정 이후 ----------


def test_same_kst_day_is_not_a_date_change():
    assert rec(kst(2026, 11, 13, 12, 0, 0)).date_changed is False


def test_arrival_just_after_midnight_departs_the_previous_evening():
    r = rec(kst(2026, 11, 14, 0, 5, 0), travel=30, buffer=0, walk_to_station_minutes=0,
            station_entry_minutes=0, exit_walk_minutes=0)

    assert r.recommended_departure_at == kst(2026, 11, 13, 23, 35, 0)
    assert r.date_changed is True


def test_departure_exactly_at_midnight_is_not_a_date_change_for_arrival_the_same_day():
    r = rec(kst(2026, 11, 14, 1, 0, 0), travel=60, buffer=0, walk_to_station_minutes=0,
            station_entry_minutes=0, exit_walk_minutes=0)

    assert r.recommended_departure_at == kst(2026, 11, 14, 0, 0, 0)
    assert r.date_changed is False


def test_year_boundary():
    r = rec(kst(2027, 1, 1, 0, 5, 0))

    assert to_kst_dict(r)["recommended_departure_at"] == "2026-12-31T23:12:00+09:00"
    assert r.date_changed is True


def test_leap_day_boundary():
    r = rec(kst(2028, 3, 1, 0, 10, 0))  # 2028은 윤년

    assert to_kst_dict(r)["recommended_departure_at"] == "2028-02-29T23:17:00+09:00"


def test_timeline_crossing_midnight_keeps_kst_dates_in_the_response():
    out = to_kst_dict(rec(kst(2026, 11, 14, 0, 10, 0)))

    starts = [s["start_at"] for s in out["timeline"]]
    assert starts[0].startswith("2026-11-13T23:17") and starts[-1].startswith("2026-11-14T00:")
    assert out["timeline"][-1]["end_at"] == "2026-11-14T00:10:00+09:00"


# ---------- 경계값 ----------


def test_minimum_total_is_one_minute():
    r = rec(travel=1, buffer=0, walk_to_station_minutes=0, station_entry_minutes=0, exit_walk_minutes=0)

    assert r.total_minutes == 1 and r.recommended_departure_at == ARRIVAL - timedelta(minutes=1)


def test_maximum_total_is_22_hours():
    r = rec(travel=720, buffer=240, walk_to_station_minutes=120, station_entry_minutes=120, exit_walk_minutes=120)

    assert r.total_minutes == 1320 and r.recommended_departure_at == ARRIVAL - timedelta(hours=22)


@pytest.mark.parametrize(
    ("field", "low", "high"),
    [
        ("travel", 1, 720),
        ("buffer", 0, 240),
        ("walk_to_station_minutes", 0, 120),
        ("station_entry_minutes", 0, 120),
        ("exit_walk_minutes", 0, 120),
    ],
)
def test_inclusive_bounds_are_accepted_and_just_outside_is_rejected(field, low, high):
    for ok in (low, high):
        assert rec(**{field: ok}).total_minutes >= 0
    for bad in (low - 1, high + 1):
        with pytest.raises(ValueError):
            rec(**{field: bad})


@pytest.mark.parametrize("bad", [True, False, 30.0, 30.5, "30", None, [30]])
@pytest.mark.parametrize("field", ["travel", "buffer", "walk_to_station_minutes", "station_entry_minutes", "exit_walk_minutes"])
def test_non_integer_minutes_are_rejected(field, bad):
    with pytest.raises(ValueError):
        rec(**{field: bad})


# ---------- 잘못된 시각 입력 ----------


@pytest.mark.parametrize(
    "bad",
    [datetime(2026, 11, 13, 9, 0, 0), "2026-11-13T09:00:00+09:00", None, 1_700_000_000, datetime(2026, 11, 13, 9, 0, 0, 500, tzinfo=KST)],
)
def test_arrival_must_be_an_aware_second_precision_datetime(bad):
    with pytest.raises(ValueError):
        recommend_departure(bad, travel_minutes=30, buffer_minutes=10)


def test_now_must_be_aware_when_given():
    with pytest.raises(ValueError):
        rec(now=datetime(2026, 11, 13, 8, 0, 0))


@pytest.mark.parametrize(
    "value",
    ["2026-11-13T09:00:00+09:00", "2026-11-13T00:00:00Z", "2026-11-13T00:00:00+00:00", "2026-11-12T19:00:00-05:00"],
)
def test_parse_arrival_iso_accepts_offset_forms(value):
    assert parse_arrival_iso(value) == ARRIVAL


@pytest.mark.parametrize(
    "bad",
    ["2026-11-13", "2026-11-13T09:00:00", "2026-11-13 09:00:00+09:00", "2026-13-40T09:00:00+09:00",
     "2026-11-13T25:00:00+09:00", "2026-11-13T09:00:00.123+09:00", "", "내일 아침", None, 123],
)
def test_parse_arrival_iso_rejects_bad_values(bad):
    with pytest.raises(ValueError):
        parse_arrival_iso(bad)


# ---------- estimated, 한계, 이미 지남 ----------


def test_result_is_always_estimated_without_timetable_and_states_limits():
    r = rec()

    assert r.estimated is True and r.timetable_used is False
    assert r.limitations == (DEP_NO_TIMETABLE, DEP_TRAVEL_EXTERNAL, DEP_SEGMENT_ESTIMATES)
    assert "시간표를 사용하지 않은" in DEP_NO_TIMETABLE and "외부에서 입력" in DEP_TRAVEL_EXTERNAL


def test_response_dict_always_carries_estimated_and_limitations():
    out = to_kst_dict(rec())

    assert out["estimated"] is True and out["timetable_used"] is False
    assert out["limitations"] and all(isinstance(m, str) for m in out["limitations"])
    assert set(out) == {"recommended_departure_at", "arrival_at", "total_minutes", "date_changed", "already_past",
                        "estimated", "timetable_used", "inputs", "timeline", "limitations"}
    assert all(v.endswith("+09:00") for v in (out["recommended_departure_at"], out["arrival_at"]))
    assert all(s["start_at"].endswith("+09:00") and s["end_at"].endswith("+09:00") for s in out["timeline"])
    json.dumps(out, ensure_ascii=False)


def test_already_past_is_none_without_now_and_flags_with_now():
    departure = kst(2026, 11, 13, 8, 7, 0)

    assert rec().already_past is None
    assert rec(now=departure - timedelta(seconds=1)).already_past is False
    assert rec(now=departure).already_past is False  # 경계: 같으면 아직 지나지 않음
    late = rec(now=departure + timedelta(seconds=1))
    assert late.already_past is True and DEP_ALREADY_PAST in late.limitations
    assert DEP_ALREADY_PAST not in rec(now=departure).limitations


# ---------- 순수성 ----------


def test_results_are_immutable_and_deterministic():
    r = rec()

    with pytest.raises(FrozenInstanceError):
        r.estimated = False
    assert rec() == rec()


def test_module_has_no_database_network_file_or_clock_access():
    tree = ast.parse(Path(dt.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = ("sqlalchemy", "app.database", "app.config", "app.models", "httpx", "socket", "requests", "os", "pathlib")
    assert not {m for m in imported for f in forbidden if m == f or m.startswith(f + ".")}
    called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    names = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "now" not in called and "utcnow" not in called and "today" not in called and "utc_now" not in names
