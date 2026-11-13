from datetime import datetime, timedelta, timezone

import pytest

from app.timeutil import (
    KST,
    UTC,
    parse_utc_iso,
    to_kst_iso,
    to_utc_iso,
    utc_now,
    utc_str_to_kst_iso,
)


def test_to_utc_iso_formats_with_z():
    dt = datetime(2026, 11, 13, 5, 0, 0, tzinfo=UTC)
    assert to_utc_iso(dt) == "2026-11-13T05:00:00Z"


def test_to_utc_iso_converts_other_offsets_to_utc():
    kst_dt = datetime(2026, 11, 13, 14, 0, 0, tzinfo=KST)
    assert to_utc_iso(kst_dt) == "2026-11-13T05:00:00Z"


def test_to_utc_iso_drops_microseconds():
    dt = datetime(2026, 11, 13, 5, 0, 0, 123456, tzinfo=UTC)
    assert to_utc_iso(dt) == "2026-11-13T05:00:00Z"


def test_naive_datetime_is_rejected():
    naive = datetime(2026, 11, 13, 5, 0, 0)
    with pytest.raises(ValueError):
        to_utc_iso(naive)
    with pytest.raises(ValueError):
        to_kst_iso(naive)


def test_parse_utc_iso_returns_aware_utc():
    parsed = parse_utc_iso("2026-11-13T05:00:00Z")
    assert parsed == datetime(2026, 11, 13, 5, 0, 0, tzinfo=UTC)
    assert parsed.utcoffset() == timedelta(0)


def test_parse_accepts_plus_zero_offset():
    assert parse_utc_iso("2026-11-13T05:00:00+00:00") == datetime(
        2026, 11, 13, 5, 0, 0, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "bad",
    [
        "2026-11-13T05:00:00",  # 타임존 없음
        "2026-11-13T14:00:00+09:00",  # UTC가 아님
        "2026-11-13 05:00:00Z",  # 형식 오류
        "not-a-time",
        "",
    ],
)
def test_parse_rejects_non_utc_or_invalid(bad):
    with pytest.raises(ValueError):
        parse_utc_iso(bad)


def test_parse_rejects_non_string():
    with pytest.raises(ValueError):
        parse_utc_iso(None)  # type: ignore[arg-type]


def test_to_kst_iso_has_plus_nine_offset():
    dt = datetime(2026, 11, 13, 5, 0, 0, tzinfo=UTC)
    assert to_kst_iso(dt) == "2026-11-13T14:00:00+09:00"


def test_utc_str_to_kst_iso_round_trip_value():
    assert utc_str_to_kst_iso("2026-11-13T05:00:00Z") == "2026-11-13T14:00:00+09:00"


def test_kst_date_boundary_at_midnight():
    # UTC 15:00은 KST 다음 날 00:00이다.
    assert utc_str_to_kst_iso("2026-11-12T15:00:00Z") == "2026-11-13T00:00:00+09:00"


def test_utc_round_trip_is_stable():
    original = "2026-11-13T05:00:00Z"
    assert to_utc_iso(parse_utc_iso(original)) == original


def test_utc_now_is_aware_utc():
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert to_utc_iso(now).endswith("Z")
