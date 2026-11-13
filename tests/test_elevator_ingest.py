"""OA-15994 엘리베이터 Upsert 적재 검증.

모든 테스트는 임시 SQLite DB를 쓴다. 실제 data/dev.db는 읽거나 쓰지 않는다.
테스트가 주입하는 fetched_at(T1, T2, ...)은 임의로 정한 값이며 실제 수집 시각이 아니다.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.clients.oa15994 import ElevatorRecord, load_sample_rows, parse_elevators
from app.models import Elevator, ElevatorStatus, Station, StationExternalId
from app.services import elevator_ingest
from app.services.elevator_ingest import (
    EmptyInputError,
    ElevatorIngestError,
    InvalidFetchedAtError,
    StaleFetchError,
    StationMappingConflictError,
    ingest_elevators,
)
from app.services.station_seed import load_seed, read_seed_dir

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "docs" / "samples" / "OA-15994_SeoulMetroFaciInfo_2026-11-13.json"
SEED = ROOT / "data" / "seed"
T1 = "2030-01-01T03:00:00Z"  # 시험용 임의 시각
T2 = "2030-01-01T04:00:00Z"
T0 = "2029-12-31T23:00:00Z"
TABLES = ("stations", "station_external_ids", "elevators", "elevator_status")


def rec(code="0150", name="엘리베이터A", status="가동중", text_="사용가능", section="B1-1F", loc="2번 출입구", station="서울역(1)"):
    return ElevatorRecord(
        station_code=code,
        station_name=station,
        elevator_name=name,
        operation_section=section,
        location_description=loc,
        source_status_text=text_,
        status=status,
    )


def unknown(code="0150", name="엘리베이터A", text_="점검"):
    return rec(code=code, name=name, status=None, text_=text_)


def snapshot(engine) -> dict:
    with engine.connect() as conn:
        return {t: conn.execute(text(f"SELECT * FROM {t} ORDER BY 1, 2")).all() for t in TABLES}


@pytest.fixture
def db(engine):
    """작은 시험 세계: 서울역(station 1)은 코드 0150·0426, 시청(station 2)은 코드 0151."""
    with Session(engine) as session:
        session.add_all([Station(station_id=1, station_name="서울역"), Station(station_id=2, station_name="시청")])
        session.flush()
        session.add_all(
            [
                StationExternalId(source="OA-15994", external_station_code="0150", station_id=1),
                StationExternalId(source="OA-15994", external_station_code="0426", station_id=1),
                StationExternalId(source="OA-15994", external_station_code="0151", station_id=2),
                # 다른 source의 같은 코드는 매핑에 쓰이지 않는다
                StationExternalId(source="OA-12035", external_station_code="0999", station_id=2),
            ]
        )
        session.commit()
        yield session


def run(session, records, fetched_at=T1, dry_run=False):
    return ingest_elevators(session, records, fetched_at=fetched_at, dry_run=dry_run)


def statuses(session) -> dict[str, ElevatorStatus]:
    rows = session.query(Elevator, ElevatorStatus).join(ElevatorStatus, ElevatorStatus.elevator_id == Elevator.elevator_id)
    return {f"{e.external_station_code}/{e.elevator_name}": s for e, s in rows}


# ---------- 1. 샘플 EV 866건 (임시 DB) ----------


@pytest.fixture
def seeded(engine):
    """시드 로더로 임시 DB에 역과 OA-15994 매핑을 넣는다. data/seed 파일만 읽는다."""
    rows, links, review = read_seed_dir(SEED)
    with Session(engine) as session:
        load_seed(session, rows, links, review)
        yield session


@pytest.fixture(scope="module")
def sample_records():
    document = json.loads(SAMPLE.read_text(encoding="utf-8"))
    return parse_elevators(load_sample_rows(document)).elevators


def test_sample_ev_866_are_loaded_into_a_temporary_db(seeded, sample_records, engine):
    report = run(seeded, sample_records)

    assert len(sample_records) == 866
    assert report.input_count == 866
    assert report.elevators_created == 866 and report.status_created == 866
    assert report.unmapped_count == 0 and report.status_unknown == 0
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM elevators")).scalar() == 866
        assert conn.execute(text("SELECT COUNT(*) FROM elevator_status")).scalar() == 866
        assert conn.execute(text("SELECT COUNT(*) FROM elevators WHERE source != 'OA-15994'")).scalar() == 0
        counts = dict(conn.execute(text("SELECT status, COUNT(*) FROM elevator_status GROUP BY status")).all())
        orphan = conn.execute(
            text("SELECT COUNT(*) FROM elevators e LEFT JOIN stations s ON s.station_id = e.station_id WHERE s.station_id IS NULL")
        ).scalar()
    assert counts == {"가동중": 845, "가동 안됨": 21}  # docs/api-notes.md 5절: EV 기준 845 / 21
    assert orphan == 0


# ---------- 2. 2회 적재 후 설비 수와 ID 불변 ----------


def test_second_load_keeps_elevator_count_and_ids(seeded, sample_records, engine):
    run(seeded, sample_records)
    with engine.connect() as conn:
        before = conn.execute(text("SELECT elevator_id, station_id, external_station_code, elevator_name FROM elevators ORDER BY elevator_id")).all()

    report = run(seeded, sample_records)  # 같은 fetched_at

    with engine.connect() as conn:
        after = conn.execute(text("SELECT elevator_id, station_id, external_station_code, elevator_name FROM elevators ORDER BY elevator_id")).all()
    assert after == before
    assert report.elevators_created == 0 and report.elevators_unchanged == 866
    assert report.status_created == 0 and report.status_unchanged == 866 and report.status_updated == 0


def test_reload_with_later_fetched_at_updates_only_fetched_at(seeded, sample_records, engine):
    run(seeded, sample_records, T1)
    with engine.connect() as conn:
        ids = conn.execute(text("SELECT elevator_id FROM elevators ORDER BY elevator_id")).all()

    report = run(seeded, sample_records, T2)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT elevator_id FROM elevators ORDER BY elevator_id")).all() == ids
        assert conn.execute(text("SELECT COUNT(*) FROM elevator_status")).scalar() == 866
        assert conn.execute(text("SELECT DISTINCT fetched_at FROM elevator_status")).all() == [(T2,)]
    assert report.status_updated == 866 and report.elevators_unchanged == 866


# ---------- 3. 상태 최신 1행 ----------


def test_status_keeps_exactly_one_latest_row_per_elevator(db, engine):
    run(db, [rec(status="가동중", text_="사용가능")], T1)
    run(db, [rec(status="가동 안됨", text_="보수중")], T2)

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT elevator_id, status, source_status_text, fetched_at FROM elevator_status")).all()
    assert rows == [(1, "가동 안됨", "보수중", T2)]  # 이력 없이 최신 1행만 있다


def test_unchanged_status_is_reported_unchanged(db):
    run(db, [rec()], T1)

    report = run(db, [rec()], T1)

    assert report.status_unchanged == 1 and report.status_updated == 0


# ---------- 4. 미확인 상태 ----------


def test_unknown_status_new_elevator_stores_only_the_elevator_row(db, engine):
    report = run(db, [unknown()], T1)

    assert report.elevators_created == 1 and report.status_unknown == 1
    assert report.status_created == 0
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM elevators")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM elevator_status")).scalar() == 0  # 가동 상태를 임의로 넣지 않는다


def test_unknown_status_keeps_existing_status_row_completely(db, engine):
    run(db, [rec(status="가동중", text_="사용가능")], T1)
    before = snapshot(engine)["elevator_status"]

    report = run(db, [unknown(text_="점검")], T2)  # 더 나중 시각으로 들어와도

    assert report.status_unknown == 1 and report.status_updated == 0 and report.status_created == 0
    assert snapshot(engine)["elevator_status"] == before  # status, source_status_text, fetched_at 모두 그대로
    assert before[0][1] == "가동중" and before[0][2] == "사용가능" and before[0][4] == T1


def test_unknown_status_still_updates_elevator_location(db):
    run(db, [rec(loc="2번 출입구")], T1)

    report = run(db, [rec(status=None, text_="점검", loc="3번 출입구")], T2)

    elevator = db.query(Elevator).one()
    assert elevator.location_description == "3번 출입구"  # 설비 정보는 갱신된다
    assert report.elevators_updated == 1


def test_mixed_batch_counts_known_and_unknown(db):
    report = run(db, [rec(name="A"), unknown(name="B"), rec(name="C", status="가동 안됨", text_="보수중")], T1)

    assert report.status_created == 2 and report.status_unknown == 1 and report.elevators_created == 3


# ---------- 5. 위치·구간 갱신 ----------


def test_location_and_section_changes_are_updated_in_place(db):
    run(db, [rec(loc="2번 출입구", section="B1-1F")], T1)
    elevator_id = db.query(Elevator).one().elevator_id

    report = run(db, [rec(loc="시청 방면5-1", section="B2-B1")], T1)

    elevator = db.query(Elevator).one()
    assert elevator.elevator_id == elevator_id  # 같은 설비 ID를 유지한다
    assert (elevator.location_description, elevator.operation_section) == ("시청 방면5-1", "B2-B1")
    assert report.elevators_updated == 1 and report.elevators_created == 0


def test_only_location_changed_counts_as_one_update(db):
    run(db, [rec()], T1)

    report = run(db, [rec(loc="다른 위치")], T1)

    assert report.elevators_updated == 1 and report.elevators_unchanged == 0


def test_empty_optional_fields_are_stored_as_null(db):
    run(db, [rec(loc=None, section=None)], T1)

    elevator = db.query(Elevator).one()
    assert elevator.location_description is None and elevator.operation_section is None


# ---------- 6. station_id 매핑 충돌 ----------


def test_station_mapping_conflict_rolls_back_everything(db, engine):
    run(db, [rec(code="0150", name="기존")], T1)
    with engine.begin() as conn:  # 매핑이 다른 역을 가리키도록 바뀐 상황
        conn.execute(
            text("UPDATE station_external_ids SET station_id = 2 WHERE source = 'OA-15994' AND external_station_code = '0150'")
        )
    before = snapshot(engine)

    batch = [
        rec(code="0151", name="새 설비"),  # 같은 배치의 정상 건
        rec(code="0150", name="기존", status="가동 안됨", text_="보수중", loc="바뀐 위치"),  # 충돌 건
    ]
    with pytest.raises(StationMappingConflictError, match="충돌"):
        run(db, batch, T2)

    assert snapshot(engine) == before  # 새 설비도, 위치·상태 변경도 하나도 남지 않았다


def test_conflict_does_not_leave_a_session_with_pending_changes(db, engine):
    run(db, [rec(code="0150", name="기존")], T1)
    with engine.begin() as conn:
        conn.execute(text("UPDATE station_external_ids SET station_id = 2 WHERE external_station_code = '0150' AND source = 'OA-15994'"))

    with pytest.raises(StationMappingConflictError):
        run(db, [rec(code="0150", name="기존")], T2)

    assert not db.new and not db.dirty  # 롤백 후 세션에 미반영 변경이 없다


# ---------- 7. 매핑 없는 코드 ----------


def test_unmapped_station_code_is_skipped_and_reported(db, engine):
    batch = [rec(code="0150", name="매핑됨"), rec(code="9999", name="매핑 없음", station="가상역"), rec(code="0999", name="다른 source 코드")]

    report = run(db, batch, T1)

    assert report.elevators_created == 1 and report.status_created == 1
    assert report.unmapped_count == 2
    assert report.unmapped_station_codes == ["0999", "9999"]  # OA-12035 코드 0999는 OA-15994 매핑이 아니다
    assert {(u.station_code, u.station_name, u.elevator_name) for u in report.unmapped} == {
        ("9999", "가상역", "매핑 없음"),
        ("0999", "서울역(1)", "다른 source 코드"),
    }
    with engine.connect() as conn:
        assert conn.execute(text("SELECT external_station_code FROM elevators")).all() == [("0150",)]


def test_unmapped_unknown_status_is_counted_as_unmapped_only(db):
    # 전부 매핑이 없는 입력은 적재할 것이 없을 뿐 오류가 아니다(빈 입력과 구분한다).
    report = run(db, [rec(code="9999")], T1)

    assert report.unmapped_count == 1 and report.elevators_created == 0 and report.status_unknown == 0


def test_existing_elevators_of_unmapped_codes_are_left_alone(db, engine):
    run(db, [rec(code="0150", name="기존")], T1)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM station_external_ids WHERE source = 'OA-15994' AND external_station_code = '0150'"))
    before = snapshot(engine)

    report = run(db, [rec(code="0150", name="기존", status="가동 안됨", text_="보수중")], T2)

    assert report.unmapped_count == 1
    assert snapshot(engine) == before


# ---------- 8. 환승역 외부 코드 ----------


def test_transfer_station_codes_stay_distinct_elevator_identifiers(db, engine):
    batch = [rec(code="0150", name="같은 이름"), rec(code="0426", name="같은 이름")]  # 같은 역(1)의 두 코드

    report = run(db, batch, T1)

    assert report.elevators_created == 2  # 이름이 같아도 코드가 다르면 서로 다른 설비
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT station_id, external_station_code, elevator_name FROM elevators ORDER BY external_station_code")).all()
    assert rows == [(1, "0150", "같은 이름"), (1, "0426", "같은 이름")]


def test_sample_keeps_distinct_external_codes_for_transfer_stations(seeded, sample_records, engine):
    run(seeded, sample_records)

    with engine.connect() as conn:
        multi_code_stations = conn.execute(
            text(
                "SELECT COUNT(*) FROM (SELECT station_id FROM elevators GROUP BY station_id "
                "HAVING COUNT(DISTINCT external_station_code) > 1)"
            )
        ).scalar()
        codes = conn.execute(text("SELECT COUNT(DISTINCT external_station_code) FROM elevators")).scalar()
    assert multi_code_stations == 32  # 한 역에 코드가 둘 이상인 환승역
    assert codes == 273


def test_same_identifier_twice_in_one_batch_is_rejected(db, engine):
    before = snapshot(engine)

    with pytest.raises(ElevatorIngestError, match="중복"):
        run(db, [rec(), rec(status="가동 안됨")], T1)

    assert snapshot(engine) == before


# ---------- 9. 빈 입력 ----------


def test_empty_input_is_an_error_and_changes_nothing(db, engine):
    run(db, [rec()], T1)
    before = snapshot(engine)

    with pytest.raises(EmptyInputError, match="0건"):
        run(db, [], T2)

    assert snapshot(engine) == before  # 기존 설비 삭제도, 상태·fetched_at 변경도 없다


def test_empty_input_is_an_error_even_for_dry_run(db):
    with pytest.raises(EmptyInputError):
        run(db, [], T1, dry_run=True)


# ---------- 10. fetched_at 검증 ----------


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "2030-01-01",
        "2030-01-01T03:00:00",  # 타임존 없음
        "2030-01-01T12:00:00+09:00",  # KST 오프셋
        "2030-01-01T03:00:00+00:00",  # UTC지만 저장 형식(Z)이 아님
        "2030-01-01 03:00:00Z",
        "2030-13-40T03:00:00Z",
        "2030-01-01T03:00:00.123Z",
        None,
        1893466800,
        b"2030-01-01T03:00:00Z",
    ],
)
def test_invalid_fetched_at_is_rejected_without_touching_the_db(db, engine, bad):
    before = snapshot(engine)

    with pytest.raises(InvalidFetchedAtError):
        run(db, [rec()], bad)

    assert snapshot(engine) == before


def test_datetime_object_is_rejected_as_fetched_at(db):
    from datetime import datetime, timezone

    with pytest.raises(InvalidFetchedAtError):
        run(db, [rec()], datetime(2030, 1, 1, 3, tzinfo=timezone.utc))


def test_valid_fetched_at_is_stored_as_injected(db):
    run(db, [rec()], "2031-02-03T04:05:06Z")

    assert db.query(ElevatorStatus).one().fetched_at == "2031-02-03T04:05:06Z"


def test_older_fetched_at_does_not_overwrite_newer_state(db, engine):
    run(db, [rec(status="가동중", text_="사용가능")], T1)
    before = snapshot(engine)

    with pytest.raises(StaleFetchError):
        run(db, [rec(status="가동 안됨", text_="보수중")], T0)

    assert snapshot(engine) == before


def test_module_never_reads_the_clock_or_the_sample_files():
    source = Path(elevator_ingest.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    body = code.split('"""', 2)[2]  # 모듈 docstring 뒤의 코드만 본다

    assert "utc_now" not in body and "datetime.now" not in body and "time.time" not in body
    assert "docs/samples" not in body and "open(" not in body and "read_text" not in body


# ---------- 11. observed_at ----------


def test_observed_at_stays_null_on_create_and_update(db, engine):
    run(db, [rec(status="가동중", text_="사용가능")], T1)
    run(db, [rec(status="가동 안됨", text_="보수중")], T2)

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT observed_at, source FROM elevator_status")).all()
    assert rows == [(None, "OA-15994")]


def test_sample_load_leaves_observed_at_null_everywhere(seeded, sample_records, engine):
    run(seeded, sample_records)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM elevator_status WHERE observed_at IS NOT NULL")).scalar() == 0


# ---------- 12. dry_run ----------


def test_dry_run_computes_counts_and_leaves_the_db_unchanged(db, engine):
    before = snapshot(engine)

    report = run(db, [rec(name="A"), unknown(name="B")], T1, dry_run=True)

    assert report.dry_run is True
    assert report.elevators_created == 2 and report.status_created == 1 and report.status_unknown == 1
    assert snapshot(engine) == before


def test_dry_run_on_existing_data_reports_updates_without_applying(db, engine):
    run(db, [rec(status="가동중", text_="사용가능", loc="A")], T1)
    before = snapshot(engine)

    report = run(db, [rec(status="가동 안됨", text_="보수중", loc="B")], T2, dry_run=True)

    assert report.elevators_updated == 1 and report.status_updated == 1
    assert snapshot(engine) == before


def test_dry_run_still_raises_on_conflict(db, engine):
    run(db, [rec(code="0150")], T1)
    with engine.begin() as conn:
        conn.execute(text("UPDATE station_external_ids SET station_id = 2 WHERE external_station_code = '0150' AND source = 'OA-15994'"))

    with pytest.raises(StationMappingConflictError):
        run(db, [rec(code="0150")], T2, dry_run=True)


# ---------- 13. 기존 설비 삭제 방지 ----------


def test_elevators_missing_from_the_input_are_not_deleted_or_changed(db, engine):
    run(db, [rec(name="남는 설비"), rec(name="사라질 설비", status="가동 안됨", text_="보수중")], T1)
    gone_before = [r for r in snapshot(engine)["elevators"] if r[4] == "사라질 설비"]
    status_before = snapshot(engine)["elevator_status"]

    report = run(db, [rec(name="남는 설비")], T2)  # 두 번째 설비가 이번 입력에서 빠졌다

    after = snapshot(engine)
    assert [r for r in after["elevators"] if r[4] == "사라질 설비"] == gone_before
    kept = [r for r in after["elevator_status"] if r[1] == "가동 안됨"]
    assert kept == [r for r in status_before if r[1] == "가동 안됨"]  # 빠진 설비의 상태·fetched_at은 그대로
    assert report.status_updated == 1 and report.elevators_unchanged == 1


# ---------- 입력·형식 방어 ----------


def test_non_record_items_are_rejected(db, engine):
    before = snapshot(engine)

    with pytest.raises(ElevatorIngestError, match="ElevatorRecord"):
        run(db, [{"STN_CD": "0150"}], T1)

    assert snapshot(engine) == before


def test_status_outside_allowed_values_is_rejected_not_stored(db, engine):
    before = snapshot(engine)
    bad = ElevatorRecord("0150", "서울역(1)", "A", "B1", "x", "고장", "고장")

    with pytest.raises(ElevatorIngestError, match="허용 값"):
        run(db, [bad], T1)

    assert snapshot(engine) == before


def test_records_may_be_any_sequence(db):
    report = run(db, tuple([rec(name="A"), rec(name="B")]), T1)

    assert report.input_count == 2


def test_report_counts_summary(db):
    report = run(db, [rec(name="A"), unknown(name="B"), rec(code="9999", name="C")], T1)

    assert report.as_counts() == {
        "input": 3,
        "elevators_created": 2,
        "elevators_updated": 0,
        "elevators_unchanged": 0,
        "status_created": 1,
        "status_updated": 0,
        "status_unchanged": 0,
        "status_unknown": 1,
        "unmapped": 1,
    }


# ---------- 호출자의 미커밋 변경에 대한 영향 (문서화된 동작을 고정한다) ----------


def make_caller_changes(session):
    """호출자가 이 함수와 무관하게 만들어 둔 미커밋 변경: 역 추가(99)와 이름 수정(1).

    아직 flush되지 않은 상태로 만든다. 먼저 조회한 뒤에 추가·수정해야 한다(조회가 뒤에 오면
    자동 flush가 일어나 DB 트랜잭션에 이미 반영된다).
    """
    station_1 = session.get(Station, 1)
    added = Station(station_id=99, station_name="호출자 미커밋")
    session.add(added)
    station_1.station_name = "호출자가 바꾼 이름"
    return added


def station_names(engine) -> dict[int, str]:
    with engine.connect() as conn:
        return dict(conn.execute(text("SELECT station_id, station_name FROM stations")).all())


def test_dry_run_also_rolls_back_the_callers_uncommitted_changes(db, engine):
    added = make_caller_changes(db)
    assert inspect(added).pending

    run(db, [rec()], T1, dry_run=True)

    assert inspect(added).transient  # 추가했던 역이 사라졌다
    assert station_names(engine) == {1: "서울역", 2: "시청"}
    assert db.get(Station, 1).station_name == "서울역"  # 수정도 되돌려졌다


def test_error_during_ingest_also_rolls_back_the_callers_uncommitted_changes(db, engine):
    run(db, [rec(code="0150", name="기존")], T1)
    with engine.begin() as conn:
        conn.execute(text("UPDATE station_external_ids SET station_id = 2 WHERE external_station_code = '0150' AND source = 'OA-15994'"))
    added = make_caller_changes(db)

    with pytest.raises(StationMappingConflictError):
        run(db, [rec(code="0150", name="기존")], T2)

    assert inspect(added).transient
    assert station_names(engine) == {1: "서울역", 2: "시청"}


def test_stale_error_also_rolls_back_the_callers_uncommitted_changes(db, engine):
    run(db, [rec()], T1)
    added = make_caller_changes(db)

    with pytest.raises(StaleFetchError):
        run(db, [rec(status="가동 안됨", text_="보수중")], T0)

    assert inspect(added).transient
    assert station_names(engine) == {1: "서울역", 2: "시청"}


def test_successful_ingest_also_commits_the_callers_uncommitted_changes(db, engine):
    make_caller_changes(db)

    run(db, [rec()], T1)

    assert station_names(engine) == {1: "호출자가 바꾼 이름", 2: "시청", 99: "호출자 미커밋"}


@pytest.mark.parametrize(
    "records, fetched_at, error",
    [
        ([], T1, EmptyInputError),
        ([rec()], "bad", InvalidFetchedAtError),
        ([rec(), rec()], T1, ElevatorIngestError),  # 중복 식별자
        (["not a record"], T1, ElevatorIngestError),
    ],
)
def test_validation_errors_leave_the_callers_uncommitted_changes_untouched(db, engine, records, fetched_at, error):
    added = make_caller_changes(db)

    with pytest.raises(error):
        run(db, records, fetched_at)

    assert inspect(added).pending  # commit도 rollback도 되지 않았다
    assert station_names(engine) == {1: "서울역", 2: "시청"}  # DB에는 아직 반영되지 않았다
    db.commit()  # 호출자가 직접 확정하면 그제야 반영된다
    assert station_names(engine) == {1: "호출자가 바꾼 이름", 2: "시청", 99: "호출자 미커밋"}


def test_using_a_separate_session_leaves_the_other_sessions_changes_alone(db, engine):
    added = make_caller_changes(db)  # 호출자 세션 A의 미커밋 변경(아직 flush 전)

    with Session(engine) as ingest_session:  # 이 함수 전용의 독립된 세션 B
        run(ingest_session, [rec()], T1)
        run(ingest_session, [rec(name="다른 설비")], T2, dry_run=True)

    assert inspect(added).pending  # A의 변경은 그대로다
    assert station_names(engine) == {1: "서울역", 2: "시청"}  # A는 아직 확정하지 않았다
    db.commit()
    assert station_names(engine) == {1: "호출자가 바꾼 이름", 2: "시청", 99: "호출자 미커밋"}
    assert db.query(Elevator).count() == 1  # B가 확정한 설비는 그대로다(dry_run 건은 없다)


def test_sqlite_blocks_a_separate_writer_while_the_caller_holds_flushed_changes(db, engine):
    """호출자 세션이 이미 flush한 미커밋 쓰기가 있으면 SQLite에서는 다른 세션의 쓰기가 막힌다."""
    from sqlalchemy import create_engine

    make_caller_changes(db)
    db.flush()  # 호출자의 미커밋 쓰기가 DB 트랜잭션에 들어가 쓰기 잠금을 쥔다
    other_engine = create_engine(engine.url, connect_args={"timeout": 0.2})
    try:
        with Session(other_engine) as ingest_session:
            with pytest.raises(Exception, match="database is locked"):
                run(ingest_session, [rec()], T1)  # 잠금 때문에 실패하고 롤백된다
    finally:
        other_engine.dispose()

    assert db.query(Elevator).count() == 0  # 독립 세션의 적재는 반영되지 않았다
    db.rollback()
    assert station_names(engine) == {1: "서울역", 2: "시청"}


def test_documented_session_behavior_is_stated_in_the_module():
    doc = elevator_ingest.__doc__
    assert "독립된 세션" in doc
    for phrase in ("성공", "dry_run", "적재 중 오류", "검증 오류", "함께 commit", "함께 rollback"):
        assert phrase in doc
    for deferred in ("샘플 입력을 실제 DB", "모든 외부 역 코드가 미매핑", "station_line_id", "별도 승인"):
        assert deferred in doc
