"""시간표 변환·조회·적재와 시간표 기반 인접 간선 추출 검증. 외부 API는 가짜 클라이언트로 대신한다."""

from dataclasses import dataclass
from datetime import date

import pytest
from sqlalchemy import func, select

from app.cli import collect_timetable, derive_edges
from app.clients.seoul_open_api import SeoulOpenApiTransportError
from app.models import StationEdge, StationExternalId, StationLine, TrainSchedule
from app.services.edge_derive import SOURCE_TIMETABLE, derive_adjacent_pairs, load_derived_edges
from app.services.station_seed import SOURCE_OA12035
from app.services.timetable import (
    TimetableError,
    day_type_for_date,
    find_last_train_departing_by,
    line_num_to_line_id,
    seconds_to_time,
    time_or_none,
    time_to_seconds,
    week_tag_to_day_type,
)
from app.services.timetable_ingest import fetch_timetables, load_timetables, parse_stop_rows, station_codes_in_db

KEY = "TESTKEY"


# ---------- 시각 변환 ----------


@pytest.mark.parametrize(
    "text,seconds",
    [("00:00:01", 1), ("05:20:30", 19230), ("23:59:59", 86399), ("24:00:00", 86400), ("24:30:00", 88200), ("25:09:30", 90570)],
)
def test_time_to_seconds_accepts_hours_past_midnight(text, seconds):
    assert time_to_seconds(text) == seconds
    assert seconds_to_time(seconds) == text


@pytest.mark.parametrize("bad", ["", "7:5", "12:60:00", "12:00:60", "48:00:00", "ab:cd:ef", "12-00-00", None, 5])
def test_time_to_seconds_rejects_bad_values(bad):
    with pytest.raises(TimetableError):
        time_to_seconds(bad)


def test_midnight_placeholder_means_none_not_zero():
    assert time_or_none("00:00:00") is None
    assert time_or_none("00:00:01") == 1


@pytest.mark.parametrize("bad", [-1, 172800, True, 1.5])
def test_seconds_to_time_rejects_out_of_range(bad):
    with pytest.raises(TimetableError):
        seconds_to_time(bad)


def test_week_tag_mapping():
    assert [week_tag_to_day_type(t) for t in ("1", "2", "3")] == ["WEEKDAY", "SATURDAY", "HOLIDAY"]
    with pytest.raises(TimetableError):
        week_tag_to_day_type("9")


def test_day_type_for_date_uses_weekday_saturday_and_sunday_or_holiday():
    assert day_type_for_date(date(2026, 11, 13)) == "WEEKDAY"  # 금
    assert day_type_for_date(date(2026, 11, 14)) == "SATURDAY"
    assert day_type_for_date(date(2026, 11, 15)) == "HOLIDAY"  # 일
    assert day_type_for_date(date(2026, 11, 13), frozenset({date(2026, 11, 13)})) == "HOLIDAY"


@pytest.mark.parametrize("text,expected", [("01호선", "1"), ("02호선", "2"), ("8호선", "8"), ("09호선", None), ("", None)])
def test_line_num_to_line_id(text, expected):
    assert line_num_to_line_id(text) == expected


# ---------- 가짜 시간표 ----------

# 2호선 강남(3) - 교대(5) - 서울대입구(6). 코드는 시험용이다.
CODES = {3: "0222", 5: "0223", 6: "0224"}


def row(code, train, arrive, leave, *, express="G", line="02호선", origin=None, dest=None):
    return {
        "STATION_CD": code, "TRAIN_NO": train, "ARRIVETIME": arrive, "LEFTTIME": leave, "LINE_NUM": line,
        "EXPRESS_YN": express, "ORIGINSTATION": origin or "", "DESTSTATION": dest or "",
    }


def schedule():
    """평일 방향 1의 시간표. T1·T2는 일반 열차(강남→교대→서울대입구), TX는 교대를 건너뛰는 급행이다."""
    rows = {code: [] for code in CODES.values()}
    for train, base in (("2001", 6 * 3600), ("2002", 6 * 3600 + 600)):
        rows["0222"].append(row("0222", train, "00:00:00", seconds_to_time(base), origin="0222"))
        rows["0223"].append(row("0223", train, seconds_to_time(base + 120), seconds_to_time(base + 150)))
        rows["0224"].append(row("0224", train, seconds_to_time(base + 270), "00:00:00", dest="0224"))
    rows["0222"].append(row("0222", "K9", "00:00:00", "07:00:00", express="D"))
    rows["0224"].append(row("0224", "K9", "07:03:00", "00:00:00", express="D"))
    return rows


@dataclass
class FakeResult:
    rows: list


class FakeClient:
    def __init__(self, data=None, fail_on=None):
        self.data = data if data is not None else schedule()
        self.fail_on = fail_on
        self.calls = []
        self.closed = False

    def fetch_all_rows(self, service, args=()):
        self.calls.append((service, args))
        code, week_tag, direction = args
        if self.fail_on == code:
            raise SeoulOpenApiTransportError("요청 시간 초과")
        if week_tag == "1" and direction == "1":
            return FakeResult(list(self.data.get(code, [])))
        return FakeResult([])

    def close(self):
        self.closed = True


@pytest.fixture
def mapped(db):
    """역 시드가 적재된 것처럼 OA-12035 외부 코드를 2호선 역-노선 행에 연결한다."""
    for station_id, code in CODES.items():
        sl = db.scalar(select(StationLine.station_line_id).where(StationLine.station_id == station_id, StationLine.line_id == "2"))
        db.add(StationExternalId(source=SOURCE_OA12035, external_station_code=code, station_id=station_id, station_line_id=sl))
    db.commit()
    return db


def sl_id(db, station_id, line="2"):
    return db.scalar(select(StationLine.station_line_id).where(StationLine.station_id == station_id, StationLine.line_id == line))


def ingest(db, client=None, **kw):
    client = client or FakeClient()
    collected = fetch_timetables(client, station_codes_in_db(db))
    return load_timetables(db, collected, fetched_at="2026-11-14T03:00:00Z", **kw)


# ---------- 파싱 ----------


def test_parse_stop_rows_converts_times_and_flags():
    parsed = parse_stop_rows(schedule()["0222"], "0222")

    assert parsed[0].departure_time == 6 * 3600 and parsed[0].arrival_time is None
    assert parsed[0].is_express is False and parsed[0].line_id == "2" and parsed[0].origin_station_code == "0222"
    assert parsed[-1].is_express is True


def test_parse_stop_rows_rejects_wrong_station_and_missing_fields():
    with pytest.raises(TimetableError, match="역 코드와 다릅니다"):
        parse_stop_rows(schedule()["0222"], "0999")
    with pytest.raises(TimetableError, match="필드가 없습니다"):
        parse_stop_rows([{"STATION_CD": "0222"}], "0222")
    with pytest.raises(TimetableError, match="모두 없습니다"):
        parse_stop_rows([row("0222", "1", "00:00:00", "00:00:00")], "0222")


def test_parse_stop_rows_keeps_past_midnight_times():
    parsed = parse_stop_rows([row("0222", "9", "24:30:00", "24:31:00")], "0222")

    assert parsed[0].arrival_time == 88200 and parsed[0].departure_time == 88260


# ---------- 적재 ----------


def test_ingest_writes_rows_and_is_idempotent(mapped):
    first = ingest(mapped)
    count = mapped.scalar(select(func.count()).select_from(TrainSchedule))
    second = ingest(mapped)

    assert first.counts["rows_written"] == count == 8
    assert mapped.scalar(select(func.count()).select_from(TrainSchedule)) == count
    assert second.counts["rows_written"] == 8
    assert first.counts["empty_groups"] > 0  # 토요일·휴일·방향 2는 비어 있다


def test_ingest_stores_integer_seconds_and_nulls_for_missing_times(mapped):
    ingest(mapped)

    origin = mapped.scalar(select(TrainSchedule).where(TrainSchedule.station_line_id == sl_id(mapped, 3), TrainSchedule.train_no == "2001"))
    terminal = mapped.scalar(select(TrainSchedule).where(TrainSchedule.station_line_id == sl_id(mapped, 6), TrainSchedule.train_no == "2001"))
    assert origin.arrival_time is None and origin.departure_time == 21600
    assert terminal.departure_time is None and terminal.arrival_time == 21870
    assert origin.service_day_type == "WEEKDAY" and origin.direction == 1 and origin.fetched_at == "2026-11-14T03:00:00Z"


def test_dry_run_rolls_back(mapped):
    report = ingest(mapped, dry_run=True)

    assert report.dry_run is True and report.counts["rows_written"] == 8
    assert mapped.scalar(select(func.count()).select_from(TrainSchedule)) == 0


def test_unmapped_station_code_is_counted_and_skipped(mapped):
    data = schedule()
    data["9999"] = [row("9999", "1", "00:00:00", "06:00:00")]
    collected = fetch_timetables(FakeClient(data), ["0222", "9999"])

    report = load_timetables(mapped, collected, fetched_at="2026-11-14T03:00:00Z")

    assert report.counts["unmapped_station_codes"] == 6  # 요일 3 x 방향 2
    assert mapped.scalar(select(func.count()).select_from(TrainSchedule)) == 3


def test_line_mismatch_rows_reject_the_whole_load(mapped):
    """#71: 불일치 행을 건너뛰면 그룹이 부분 스냅샷이 되므로 전체를 거부한다. 자세한 검증은 test_timetable_integrity.py."""
    data = schedule()
    data["0222"] = [row("0222", "1", "00:00:00", "06:00:00", line="03호선")]

    with pytest.raises(TimetableError, match="노선"):
        load_timetables(mapped, fetch_timetables(FakeClient(data), ["0222"]), fetched_at="2026-11-14T03:00:00Z")

    assert mapped.scalar(select(func.count()).select_from(TrainSchedule)) == 0


def test_reingest_replaces_a_group_instead_of_adding(mapped):
    ingest(mapped)
    data = schedule()
    data["0222"] = [row("0222", "7777", "00:00:00", "08:00:00", origin="0222")]

    load_timetables(mapped, fetch_timetables(FakeClient(data), ["0222"]), fetched_at="2026-11-14T04:00:00Z")

    trains = set(mapped.scalars(select(TrainSchedule.train_no).where(TrainSchedule.station_line_id == sl_id(mapped, 3))))
    assert trains == {"7777"}


def test_duplicate_train_number_rolls_everything_back(mapped):
    ingest(mapped)
    before = mapped.scalar(select(func.count()).select_from(TrainSchedule))
    data = schedule()
    data["0223"] = [row("0223", "1", "06:00:00", "06:00:30"), row("0223", "1", "07:00:00", "07:00:30")]

    with pytest.raises(TimetableError, match="중복"):
        load_timetables(mapped, fetch_timetables(FakeClient(data), ["0222", "0223"]), fetched_at="2026-11-14T05:00:00Z")

    assert mapped.scalar(select(func.count()).select_from(TrainSchedule)) == before
    assert mapped.scalar(select(func.max(TrainSchedule.fetched_at))) == "2026-11-14T03:00:00Z"  # 기존 데이터 유지


def test_loop_terminal_rows_are_merged_into_one_row(mapped):
    data = schedule()
    data["0223"] = [
        row("0223", "2022", "00:00:00", "05:30:00", origin="0223", dest="0223"),
        row("0223", "2022", "06:58:00", "00:00:00", origin="0223", dest="0223"),
    ]

    report = load_timetables(mapped, fetch_timetables(FakeClient(data), ["0223"]), fetched_at="2026-11-14T03:00:00Z")

    stored = mapped.scalars(select(TrainSchedule).where(TrainSchedule.train_no == "2022")).all()
    assert len(stored) == 1 and report.counts["loop_terminal_merged"] == 1
    assert stored[0].departure_time == 5 * 3600 + 30 * 60 and stored[0].arrival_time == 6 * 3600 + 58 * 60


def test_duplicate_that_is_not_a_loop_terminal_pair_still_fails(mapped):
    data = schedule()
    data["0223"] = [
        row("0223", "2022", "00:00:00", "05:30:00", origin="0223", dest="0223"),
        row("0223", "2022", "00:00:00", "07:30:00", origin="0223", dest="0223"),
    ]

    with pytest.raises(TimetableError, match="중복"):
        load_timetables(mapped, fetch_timetables(FakeClient(data), ["0223"]), fetched_at="2026-11-14T03:00:00Z")


def test_failed_fetch_raises_before_any_write(mapped):
    with pytest.raises(SeoulOpenApiTransportError):
        fetch_timetables(FakeClient(fail_on="0223"), station_codes_in_db(mapped))

    assert mapped.scalar(select(func.count()).select_from(TrainSchedule)) == 0


# ---------- 조회 ----------


def test_find_last_train_departing_by(mapped):
    ingest(mapped)
    args = dict(station_line_id=sl_id(mapped, 3), day_type="WEEKDAY", direction=1)

    assert find_last_train_departing_by(mapped, latest_departure_seconds=6 * 3600 + 300, **args).train_no == "2001"
    assert find_last_train_departing_by(mapped, latest_departure_seconds=6 * 3600 + 600, **args).train_no == "2002"  # 같은 시각 포함
    assert find_last_train_departing_by(mapped, latest_departure_seconds=6 * 3600 - 1, **args) is None


def test_lookup_skips_terminal_rows_without_departure(mapped):
    ingest(mapped)

    found = find_last_train_departing_by(
        mapped, station_line_id=sl_id(mapped, 6), day_type="WEEKDAY", direction=1, latest_departure_seconds=86399
    )

    assert found is None  # 종착역은 출발 시각이 없다


def test_lookup_validates_arguments(mapped):
    with pytest.raises(TimetableError):
        find_last_train_departing_by(mapped, station_line_id=1, day_type="MONDAY", direction=1, latest_departure_seconds=0)
    with pytest.raises(TimetableError):
        find_last_train_departing_by(mapped, station_line_id=1, day_type="WEEKDAY", direction=3, latest_departure_seconds=0)


def test_lookup_handles_service_day_past_midnight(mapped):
    data = schedule()
    data["0222"] = [row("0222", "N1", "00:00:00", "24:30:00", origin="0222")]
    load_timetables(mapped, fetch_timetables(FakeClient(data), ["0222"]), fetched_at="2026-11-14T03:00:00Z")

    found = find_last_train_departing_by(
        mapped, station_line_id=sl_id(mapped, 3), day_type="WEEKDAY", direction=1, latest_departure_seconds=time_to_seconds("24:45:00")
    )

    assert found.train_no == "N1" and found.departure_time == 88200


# ---------- 인접 간선 추출 ----------


def edges(db):
    return {(a, b, t, s) for a, b, t, s in db.execute(
        select(StationEdge.from_station_line_id, StationEdge.to_station_line_id, StationEdge.edge_type, StationEdge.source))}


def test_derive_pairs_from_consecutive_stops_and_skips_express(mapped):
    ingest(mapped)

    pairs, stats = derive_adjacent_pairs(mapped)

    a, b, c = sl_id(mapped, 3), sl_id(mapped, 5), sl_id(mapped, 6)
    assert pairs == {(min(a, b), max(a, b)): 2, (min(b, c), max(b, c)): 2}  # 강남-서울대입구는 급행이라 없다
    assert (min(a, c), max(a, c)) not in pairs
    assert stats["express_rows_skipped"] == 2 and stats["trains_scanned"] == 2


def test_derive_requires_min_trains(mapped):
    data = schedule()
    for code in data:
        data[code] = [r for r in data[code] if r["TRAIN_NO"] != "2002"]
    load_timetables(mapped, fetch_timetables(FakeClient(data), station_codes_in_db(mapped)), fetched_at="2026-11-14T03:00:00Z")

    pairs, stats = derive_adjacent_pairs(mapped)

    assert pairs == {} and stats["pairs_below_min_trains"] == 2
    assert len(derive_adjacent_pairs(mapped, min_trains=1)[0]) == 2


def test_derive_drops_long_gaps(mapped):
    data = schedule()
    data["0223"] = [row("0223", t, "07:30:00", "07:30:30") for t in ("2001", "2002")]  # 강남(06:00) 뒤 90분
    load_timetables(mapped, fetch_timetables(FakeClient(data), station_codes_in_db(mapped)), fetched_at="2026-11-14T03:00:00Z")

    pairs, stats = derive_adjacent_pairs(mapped)

    assert stats["gap_too_long_skipped"] >= 2
    assert (min(sl_id(mapped, 3), sl_id(mapped, 5)), max(sl_id(mapped, 3), sl_id(mapped, 5))) not in pairs


def test_derive_skips_pairs_across_lines(mapped):
    other = sl_id(mapped, 5, "3")
    mapped.add(TrainSchedule(station_line_id=other, service_day_type="WEEKDAY", direction=1, train_no="2001",
                             arrival_time=21500, departure_time=21510, is_express=False, fetched_at="2026-11-14T03:00:00Z"))
    mapped.commit()
    ingest(mapped)

    _, stats = derive_adjacent_pairs(mapped)

    assert stats["cross_line_skipped"] >= 1


def test_load_derived_edges_is_idempotent_and_marks_source(mapped):
    ingest(mapped)
    first = load_derived_edges(mapped)
    second = load_derived_edges(mapped)

    assert first.counts["adjacent_created"] == 2 and second.counts["adjacent_created"] == 0
    assert second.counts["adjacent_unchanged"] == 2
    assert {(t, s) for _, _, t, s in edges(mapped)} == {("ADJACENT", SOURCE_TIMETABLE)}


def test_load_derived_edges_never_overwrites_existing(mapped):
    ingest(mapped)
    a, b = sorted((sl_id(mapped, 3), sl_id(mapped, 5)))
    mapped.add(StationEdge(from_station_line_id=b, to_station_line_id=a, edge_type="ADJACENT", source="OA-12035+FE-MOCK"))
    mapped.commit()

    report = load_derived_edges(mapped)

    assert report.counts["adjacent_kept_other_source"] == 1 and report.counts["adjacent_created"] == 1
    assert ("ADJACENT", "OA-12035+FE-MOCK") in {(t, s) for *_, t, s in edges(mapped)}


def test_load_derived_edges_dry_run_rolls_back(mapped):
    ingest(mapped)

    report = load_derived_edges(mapped, dry_run=True)

    assert report.counts["adjacent_created"] == 2
    assert mapped.scalar(select(func.count()).select_from(StationEdge)) == 0


def add_stops(db, train, stops, day="WEEKDAY"):
    for sl, when in stops:
        db.add(TrainSchedule(station_line_id=sl, service_day_type=day, direction=1, train_no=train,
                             arrival_time=when, departure_time=when, is_express=False, fetched_at="2026-11-14T03:00:00Z"))


def add_normal_and_nonstop_trains(db):
    a, b, c = sl_id(db, 3), sl_id(db, 5), sl_id(db, 6)
    for i in range(12):
        base = 6 * 3600 + i * 600
        add_stops(db, f"N{i}", [(a, base), (b, base + 120), (c, base + 270)])
    for i in range(2):  # 급행 표시 없는 무정차 열차(회송·입출고로 추정)
        base = 22 * 3600 + i * 600
        add_stops(db, f"S{i}", [(a, base), (c, base + 270)])
    db.commit()
    return a, b, c


def test_derive_drops_pairs_only_nonstop_trains_support(mapped):
    a, b, c = add_normal_and_nonstop_trains(mapped)

    pairs, stats = derive_adjacent_pairs(mapped)

    assert set(pairs) == {(min(a, b), max(a, b)), (min(b, c), max(b, c))}
    assert (min(a, c), max(a, c)) not in pairs and stats["pairs_below_relative_support"] == 1


def test_prune_removes_stale_derived_edges_only(mapped):
    a, b, c = add_normal_and_nonstop_trains(mapped)
    mapped.add(StationEdge(from_station_line_id=min(a, c), to_station_line_id=max(a, c), edge_type="ADJACENT", source=SOURCE_TIMETABLE))
    mapped.add(StationEdge(from_station_line_id=sl_id(mapped, 5, "3"), to_station_line_id=b, edge_type="TRANSFER", source="OA-13290"))
    mapped.commit()

    report = load_derived_edges(mapped, prune=True)

    kept = {(t, s) for *_, t, s in edges(mapped)}
    assert report.counts["adjacent_pruned"] == 1 and ("TRANSFER", "OA-13290") in kept
    assert (min(a, c), max(a, c), "ADJACENT", SOURCE_TIMETABLE) not in edges(mapped)
    assert load_derived_edges(mapped).counts["adjacent_pruned"] == 0


def test_derived_edges_feed_the_route_api(client, mapped):
    ingest(mapped)
    load_derived_edges(mapped)

    body = client.get("/api/routes", params={"from": 3, "to": 6}).json()

    assert [s["station_id"] for s in body["stations"]] == [3, 5, 6]
    assert body["total_minutes"] == 4


# ---------- CLI ----------


@pytest.fixture
def db_url(engine, mapped, monkeypatch):
    return str(engine.url)


def run_collect(db_url, *args, client=None, key=KEY):
    fake = client or FakeClient()
    code = collect_timetable.main(
        ["--database-url", db_url, *args], client_factory=lambda k, base: fake, api_key=key
    )
    return code, fake


def test_collect_cli_dry_run_changes_nothing(db_url, mapped, capsys):
    code, fake = run_collect(db_url)

    assert code == 0 and "[dry-run]" in capsys.readouterr().out
    assert fake.closed is True and len(fake.calls) == 3 * 3 * 2
    mapped.expire_all()
    assert mapped.scalar(select(func.count()).select_from(TrainSchedule)) == 0


def test_collect_cli_apply_writes_and_never_prints_key(db_url, mapped, capsys):
    code, _ = run_collect(db_url, "--apply")

    out = capsys.readouterr()
    assert code == 0 and "rows_written: 8" in out.out
    assert KEY not in out.out + out.err
    mapped.expire_all()
    assert mapped.scalar(select(func.count()).select_from(TrainSchedule)) == 8


def test_collect_cli_station_code_and_day_type_filters(db_url, mapped):
    _, fake = run_collect(db_url, "--station-code", "0222", "--day-type", "WEEKDAY")

    assert {args[0] for _, args in fake.calls} == {"0222"} and len(fake.calls) == 2


def test_collect_cli_api_failure_keeps_data_and_exit_3(db_url, mapped, capsys):
    ingest(mapped)
    code, _ = run_collect(db_url, "--apply", client=FakeClient(fail_on="0223"))

    assert code == collect_timetable.EXIT_API_ERROR
    assert "기존 데이터는 그대로" in capsys.readouterr().err
    mapped.expire_all()
    assert mapped.scalar(select(func.count()).select_from(TrainSchedule)) == 8


def test_collect_cli_requires_key_and_valid_address(db_url, capsys):
    assert run_collect(db_url, key="")[0] == collect_timetable.EXIT_NOT_READY
    assert run_collect(db_url, "--base-url", "ftp://x.test")[0] == collect_timetable.EXIT_NOT_READY
    assert run_collect(db_url, "--base-url", "http://mock.test:8088")[0] == 0  # 평문 HTTP 허용


def test_collect_cli_reports_missing_database(tmp_path, capsys):
    code = collect_timetable.main(["--database-url", f"sqlite:///{tmp_path / 'none.db'}"], api_key=KEY, client_factory=lambda *a: FakeClient())

    assert code == collect_timetable.EXIT_NOT_READY and "SQLite 파일이 없습니다" in capsys.readouterr().err


def test_derive_cli_requires_timetable_then_applies(db_url, mapped, capsys):
    assert derive_edges.main(["--database-url", db_url]) == derive_edges.EXIT_NOT_READY
    assert "비어 있습니다" in capsys.readouterr().err

    run_collect(db_url, "--apply")
    assert derive_edges.main(["--database-url", db_url]) == 0
    assert "[dry-run]" in capsys.readouterr().out
    mapped.expire_all()
    assert mapped.scalar(select(func.count()).select_from(StationEdge)) == 0

    assert derive_edges.main(["--database-url", db_url, "--apply"]) == 0
    assert "adjacent_created: 2" in capsys.readouterr().out
    mapped.expire_all()
    assert mapped.scalar(select(func.count()).select_from(StationEdge)) == 2


def add_stale_derived_edge(db):
    a, c = sl_id(db, 3), sl_id(db, 6)
    db.add(StationEdge(from_station_line_id=min(a, c), to_station_line_id=max(a, c), edge_type="ADJACENT", source=SOURCE_TIMETABLE))
    db.commit()
    return (min(a, c), max(a, c), "ADJACENT", SOURCE_TIMETABLE)


def test_prune_is_refused_when_only_some_stations_are_collected(db_url, mapped, capsys):
    run_collect(db_url, "--apply", "--station-code", "0222")
    stale = add_stale_derived_edge(mapped)

    for mode in ("--apply", "--dry-run"):
        code = derive_edges.main(["--database-url", db_url, mode, "--prune"])
        out = capsys.readouterr()
        assert code == derive_edges.EXIT_NOT_READY and "--prune을 거부" in out.err
        assert "시간표 커버리지: 1/3" in out.out

    mapped.expire_all()
    assert stale in edges(mapped)


def test_prune_is_refused_when_a_day_type_is_missing(db_url, mapped, capsys):
    run_collect(db_url, "--apply")  # 세 역 모두 있지만 평일 시간표뿐이다
    stale = add_stale_derived_edge(mapped)

    code = derive_edges.main(["--database-url", db_url, "--apply", "--prune"])

    out = capsys.readouterr()
    assert code == derive_edges.EXIT_NOT_READY and "시간표 커버리지: 3/3" in out.out and "빠진 요일: SATURDAY, HOLIDAY" in out.out
    mapped.expire_all()
    assert stale in edges(mapped)


def test_prune_is_allowed_with_full_coverage_and_keeps_other_sources(db_url, mapped, capsys):
    run_collect(db_url, "--apply")
    a = sl_id(mapped, 3)
    add_stops(mapped, "S1", [(a, 6 * 3600)], day="SATURDAY")
    add_stops(mapped, "H1", [(a, 6 * 3600)], day="HOLIDAY")
    mapped.add(StationEdge(from_station_line_id=sl_id(mapped, 5, "3"), to_station_line_id=sl_id(mapped, 5), edge_type="TRANSFER", source="OA-13290"))
    mapped.commit()
    stale = add_stale_derived_edge(mapped)

    code = derive_edges.main(["--database-url", db_url, "--apply", "--prune"])

    out = capsys.readouterr().out
    assert code == 0 and "빠진 요일: 없음 -> --prune 허용" in out and "adjacent_pruned: 1" in out
    mapped.expire_all()
    assert stale not in edges(mapped)
    assert {s for *_, s in edges(mapped)} == {SOURCE_TIMETABLE, "OA-13290"}


def test_coverage_is_printed_without_prune_even_when_insufficient(db_url, mapped, capsys):
    run_collect(db_url, "--apply", "--station-code", "0222")

    code = derive_edges.main(["--database-url", db_url])

    out = capsys.readouterr().out
    assert code == 0 and "시간표 커버리지: 1/3" in out and "--prune 거부 대상" in out
