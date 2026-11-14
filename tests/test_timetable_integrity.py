"""시간표 적재 실패 시 기존 데이터 보존 검증. 이슈 #71. 외부 API와 운영 DB는 쓰지 않는다."""

import pytest
from sqlalchemy import func, select

from app.cli import collect_timetable
from app.models import TrainSchedule
from app.services.timetable import TimetableError
from app.services.timetable_ingest import (
    fetch_timetables,
    load_timetables,
    normalize_fetched_at,
    parse_stop_rows,
    station_codes_in_db,
)
from tests.test_timetable import KEY, FakeClient, mapped, row, schedule, sl_id  # noqa: F401  (mapped는 픽스처)

T0 = "2026-11-14T03:00:00Z"
T1 = "2026-11-14T04:00:00Z"


def snapshot(db):
    """적재된 모든 행의 내용과 fetched_at."""
    db.expire_all()
    return sorted(
        db.execute(
            select(
                TrainSchedule.station_line_id, TrainSchedule.service_day_type, TrainSchedule.direction,
                TrainSchedule.train_no, TrainSchedule.arrival_time, TrainSchedule.departure_time,
                TrainSchedule.origin_station_code, TrainSchedule.destination_station_code,
                TrainSchedule.is_express, TrainSchedule.fetched_at,
            )
        ).all()
    )


def load(db, data, codes=None, fetched_at=T1, **kw):
    collected = fetch_timetables(FakeClient(data), codes or station_codes_in_db(db))
    return load_timetables(db, collected, fetched_at=fetched_at, **kw)


@pytest.fixture
def loaded(mapped):  # noqa: F811
    """기존 데이터가 있는 DB(평일 방향 1 8행, fetched_at=T0)."""
    load(mapped, schedule(), fetched_at=T0)
    before = snapshot(mapped)
    assert len(before) == 8
    return mapped, before


# ---------- 노선 불일치·미확인 ----------


def test_group_with_all_rows_on_another_line_is_rejected_and_existing_rows_kept(loaded):
    db, before = loaded
    data = schedule()
    data["0222"] = [{**r, "LINE_NUM": "03호선"} for r in data["0222"]]

    with pytest.raises(TimetableError, match="노선"):
        load(db, data)

    assert snapshot(db) == before


def test_mixed_valid_and_mismatched_group_is_not_partially_replaced(loaded):
    db, before = loaded
    data = schedule()
    data["0222"][0] = {**data["0222"][0], "LINE_NUM": "03호선"}

    with pytest.raises(TimetableError, match="노선"):
        load(db, data)

    assert snapshot(db) == before


@pytest.mark.parametrize("line", ["", "09호선", "경의선", None])
def test_unknown_line_rows_are_rejected(loaded, line):
    db, before = loaded
    data = schedule()
    first = dict(data["0223"][0])
    if line is None:
        del first["LINE_NUM"]
    else:
        first["LINE_NUM"] = line
    data["0223"][0] = first

    with pytest.raises(TimetableError):
        load(db, data)

    assert snapshot(db) == before


def test_mismatch_in_later_group_preserves_all_previous_rows_and_fetched_at(loaded):
    db, before = loaded
    data = schedule()
    data["0222"] = [row("0222", "7777", "00:00:00", "08:00:00", origin="0222")]  # 앞 그룹은 정상
    data["0224"][-1] = {**data["0224"][-1], "LINE_NUM": "05호선"}  # 마지막 역에서 불일치

    with pytest.raises(TimetableError):
        load(db, data)

    assert snapshot(db) == before
    assert {r[-1] for r in snapshot(db)} == {T0}


def test_dry_run_with_mismatch_also_raises(loaded):
    db, before = loaded
    data = schedule()
    data["0222"][0] = {**data["0222"][0], "LINE_NUM": "03호선"}

    with pytest.raises(TimetableError):
        load(db, data, dry_run=True)

    assert snapshot(db) == before


# ---------- 파서 필드 타입 ----------


@pytest.mark.parametrize(
    "field,value",
    [
        ("TRAIN_NO", 2001),
        ("TRAIN_NO", None),
        ("TRAIN_NO", "20 01"),
        ("TRAIN_NO", "<script>"),
        ("TRAIN_NO", "X" * 17),
        ("STATION_CD", 222),
        ("LINE_NUM", 2),
        ("LINE_NUM", ["02호선"]),
        ("ORIGINSTATION", 222),
        ("ORIGINSTATION", "02 22"),
        ("DESTSTATION", {"a": 1}),
        ("EXPRESS_YN", True),
        ("EXPRESS_YN", "Y"),
        ("EXPRESS_YN", 1),
        ("ARRIVETIME", 0),
        ("LEFTTIME", None),
    ],
)
def test_parser_rejects_bad_field_types_with_timetable_error(field, value):
    bad = {**row("0222", "2001", "00:00:00", "06:00:00", origin="0222"), field: value}

    with pytest.raises(TimetableError):
        parse_stop_rows([bad], "0222")


@pytest.mark.parametrize("bad", [None, "row", 3, ["0222"]])
def test_parser_rejects_non_object_rows(bad):
    with pytest.raises(TimetableError):
        parse_stop_rows([bad], "0222")


def test_parser_accepts_documented_values():
    parsed = parse_stop_rows(
        [
            row("0222", "K1902", "00:00:00", "06:00:00", express="D", origin="1728", dest="0150"),
            row("0222", "2001", "06:00:00", "06:00:30", express="G"),
        ],
        "0222",
    )

    assert parsed[0].is_express is True and parsed[0].origin_station_code == "1728"
    assert parsed[1].is_express is False and parsed[1].origin_station_code is None


def test_bad_field_type_keeps_existing_data_through_cli(db_url, loaded, capsys):
    db, before = loaded
    data = schedule()
    data["0223"][0] = {**data["0223"][0], "TRAIN_NO": 2001}

    code, _ = run_collect(db_url, "--apply", client=FakeClient(data))

    assert code == collect_timetable.EXIT_DATA_ERROR
    assert snapshot(db) == before


# ---------- fetched_at ----------


@pytest.mark.parametrize(
    "value,expected",
    [("2026-11-14T03:00:00Z", "2026-11-14T03:00:00Z"), ("2026-11-14T03:00:00+00:00", "2026-11-14T03:00:00Z")],
)
def test_normalize_fetched_at(value, expected):
    assert normalize_fetched_at(value) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "2026-11-14T12:00:00+09:00",
        "2026-11-14T03:00:00",
        "2026-11-14T03:00:00.123Z",
        "2026-11-14 03:00:00Z",
        "2026-13-14T03:00:00Z",
        "",
        None,
        1731553200,
    ],
)
def test_bad_fetched_at_is_rejected_and_nothing_changes(loaded, bad):
    db, before = loaded

    with pytest.raises(TimetableError):
        load(db, schedule(), fetched_at=bad)

    assert snapshot(db) == before


def test_plus_zero_offset_is_stored_as_z(mapped):  # noqa: F811
    load(mapped, schedule(), fetched_at="2026-11-14T05:00:00+00:00")

    assert {r[-1] for r in snapshot(mapped)} == {"2026-11-14T05:00:00Z"}


def test_older_fetched_at_cannot_overwrite_newer_data(loaded):
    db, before = loaded
    data = schedule()
    data["0222"] = [row("0222", "7777", "00:00:00", "08:00:00", origin="0222")]

    with pytest.raises(TimetableError, match="오래된|이전"):
        load(db, data, fetched_at="2026-11-14T02:59:59Z")

    assert snapshot(db) == before


def test_older_empty_result_does_not_delete_newer_group(loaded):
    db, before = loaded
    data = schedule()
    data["0222"] = []

    with pytest.raises(TimetableError):
        load(db, data, fetched_at="2026-11-13T00:00:00Z")

    assert snapshot(db) == before


def test_same_fetched_at_rerun_is_allowed_and_idempotent(loaded):
    db, before = loaded

    report = load(db, schedule(), fetched_at=T0)

    assert report.counts["rows_written"] == 8 and snapshot(db) == before


def test_newer_fetched_at_replaces(loaded):
    db, _ = loaded
    data = schedule()
    data["0222"] = [row("0222", "7777", "00:00:00", "08:00:00", origin="0222")]

    load(db, data, fetched_at=T1)

    trains = set(db.scalars(select(TrainSchedule.train_no).where(TrainSchedule.station_line_id == sl_id(db, 3))))
    assert trains == {"7777"}


# ---------- 유지되어야 하는 동작 ----------


def test_genuinely_empty_result_still_deletes_the_group(loaded):
    db, _ = loaded
    data = schedule()
    data["0222"] = []

    report = load(db, data, fetched_at=T1)

    assert db.scalar(select(func.count()).select_from(TrainSchedule).where(TrainSchedule.station_line_id == sl_id(db, 3))) == 0
    assert report.counts["empty_groups"] > 0
    assert db.scalar(select(func.count()).select_from(TrainSchedule)) == 5


def test_loop_terminal_merge_still_works(mapped):  # noqa: F811
    data = schedule()
    data["0223"] = [
        row("0223", "2022", "00:00:00", "05:30:00", origin="0223", dest="0223"),
        row("0223", "2022", "06:58:00", "00:00:00", origin="0223", dest="0223"),
    ]

    report = load(mapped, data, codes=["0223"], fetched_at=T0)

    stored = mapped.scalars(select(TrainSchedule).where(TrainSchedule.train_no == "2022")).all()
    assert len(stored) == 1 and report.counts["loop_terminal_merged"] == 1
    assert "line_mismatch_skipped" not in report.counts


# ---------- CLI ----------


@pytest.fixture
def db_url(engine, mapped):  # noqa: F811
    return str(engine.url)


def run_collect(db_url, *args, client=None, key=KEY):
    fake = client or FakeClient()
    code = collect_timetable.main(["--database-url", db_url, *args], client_factory=lambda k, base: fake, api_key=key)
    return code, fake


def test_cli_line_mismatch_is_data_error_and_keeps_data_without_leaking_key(db_url, loaded, capsys):
    db, before = loaded
    data = schedule()
    data["0222"] = [{**r, "LINE_NUM": "03호선"} for r in data["0222"]]

    code, _ = run_collect(db_url, "--apply", "--base-url", "http://mock.test:8088", client=FakeClient(data))

    out = capsys.readouterr()
    text = out.out + out.err
    assert code == collect_timetable.EXIT_DATA_ERROR
    assert "롤백" in out.err
    assert KEY not in text and "mock.test" not in text and "http" not in text
    assert snapshot(db) == before
