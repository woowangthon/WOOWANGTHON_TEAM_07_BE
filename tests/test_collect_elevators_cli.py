"""collect_elevators CLI 검증.

임시 SQLite DB와 가짜 클라이언트만 쓴다. 실제 외부 API 호출, 실제 data/dev.db 접근, 실제 키 사용은 없다.
(키는 가짜 값이고, 테스트 중 외부 네트워크 연결은 conftest의 가드가 막는다.)
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.cli import collect_elevators as cli
from app.cli.collect_elevators import (
    EXIT_API_ERROR,
    EXIT_DATA_ERROR,
    EXIT_LOG_FAILED,
    EXIT_NOT_READY,
    EXIT_OK,
    main,
)
from app.clients.seoul_open_api import (
    FetchResult,
    SeoulOpenApiConfigError,
    SeoulOpenApiResponseError,
    SeoulOpenApiTransportError,
)
from app.database import make_engine as real_make_engine
from app.models import Station, StationExternalId
from app.services.ingestion_log import IngestionLogError
from app.services.station_seed import load_seed, read_seed_dir

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "docs" / "samples" / "OA-15994_SeoulMetroFaciInfo_2026-11-13.json"
SEED = ROOT / "data" / "seed"
KEY = "FAKEKEY-abc123XYZ"
HTTPS = "https://mock.test"
T1 = "2030-01-01T03:00:00Z"  # 시험용 임의 시각
T2 = "2030-01-01T04:00:00Z"
CLOCK_T1 = lambda: datetime(2030, 1, 1, 3, 0, 0, tzinfo=timezone.utc)  # noqa: E731
CLOCK_T2 = lambda: datetime(2030, 1, 1, 4, 0, 0, tzinfo=timezone.utc)  # noqa: E731
TABLES = ("stations", "station_external_ids", "elevators", "elevator_status")
JSON_KEYS = {
    "schema_version", "ok", "partial_success", "exit_code", "run_id", "mode", "dry_run", "committed",
    "fetched_at", "counts", "unmapped_station_codes", "log", "warnings", "error",
}
COUNT_KEYS = {
    "input_rows", "ev_elevators", "excluded_by_type", "elevators_created", "elevators_updated",
    "elevators_unchanged", "status_created", "status_updated", "status_unchanged", "status_unknown", "unmapped",
}


def row(code="0150", name="엘리베이터A", use_yn="사용가능", se="EV", station="서울역(1)", loc="2번 출입구", section="B1-1F"):
    return {
        "STN_CD": code, "STN_NM": station, "ELVTR_NM": name, "OPR_SEC": section,
        "INSTL_PSTN": loc, "USE_YN": use_yn, "ELVTR_SE": se,
    }


def db_url(engine) -> str:
    return engine.url.render_as_string(hide_password=False)


@pytest.fixture
def world(engine):
    """작은 시험 세계: 서울역(1)은 코드 0150·0426, 시청(2)은 0151이 OA-15994로 매핑되어 있다."""
    with Session(engine) as session:
        session.add_all([Station(station_id=1, station_name="서울역"), Station(station_id=2, station_name="시청")])
        session.flush()
        session.add_all(
            [
                StationExternalId(source="OA-15994", external_station_code="0150", station_id=1),
                StationExternalId(source="OA-15994", external_station_code="0426", station_id=1),
                StationExternalId(source="OA-15994", external_station_code="0151", station_id=2),
            ]
        )
        session.commit()
    return engine


def snapshot(engine) -> dict:
    with engine.connect() as conn:
        return {t: conn.execute(text(f"SELECT * FROM {t} ORDER BY 1, 2")).all() for t in TABLES}


def count(engine, table: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()


class FakeClient:
    def __init__(self, rows=None, error=None, on_fetch=None):
        self.rows, self.error, self.on_fetch = rows or [], error, on_fetch
        self.fetched, self.closed = 0, False

    def fetch_all_rows(self, service):
        self.fetched += 1
        if self.on_fetch:
            self.on_fetch()
        if self.error:
            raise self.error
        return FetchResult(rows=self.rows, total=len(self.rows), pages=1)

    def close(self):
        self.closed = True


class Run:
    def __init__(self, code, out, err, calls, client, log_path):
        self.code, self.out, self.err, self.calls, self.client, self.log_path = code, out, err, calls, client, log_path

    @property
    def log_lines(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]

    @property
    def blob(self) -> str:
        log = self.log_path.read_text(encoding="utf-8") if self.log_path.exists() else ""
        return self.out + self.err + log

    def json(self) -> dict:
        return json.loads(self.out)


@pytest.fixture
def invoke(capsys, tmp_path):
    def _invoke(args, *, rows=None, error=None, on_fetch=None, clock=CLOCK_T1, key=KEY, run_id="run-1", log_path=None):
        client = FakeClient(rows=rows, error=error, on_fetch=on_fetch)
        calls: list[tuple[str, str]] = []

        def factory(api_key, base_url):
            calls.append((api_key, base_url))
            return client

        path = log_path or (tmp_path / "logs" / "issues.jsonl")
        if log_path is None:
            # 로그 디렉터리를 미리 만들어 둔다. 디렉터리가 없으면 의도치 않은 기록이 실패해서 파일이 안 생기므로,
            # "dry-run은 로그를 쓰지 않는다" 같은 단언이 우연히 통과하는 것을 막는다.
            path.parent.mkdir(parents=True, exist_ok=True)
        code = main(args, client_factory=factory, clock=clock, log_path=path, api_key=key, run_id_factory=lambda: run_id)
        captured = capsys.readouterr()
        return Run(code, captured.out, captured.err, calls, client, path)

    return _invoke


def api_args(engine, *extra):
    return ["--base-url", HTTPS, "--database-url", db_url(engine), *extra]


def file_args(engine, path, *extra, fetched_at=T1):
    return ["--input-file", str(path), "--fetched-at", fetched_at, "--database-url", db_url(engine), *extra]


@pytest.fixture
def sample_file(tmp_path):
    path = tmp_path / "input.json"
    document = {"service": "SeoulMetroFaciInfo", "list_total_count": 3, "row": [row(name="A"), row(name="B", use_yn="보수중"), row(code="0151", name="C", station="시청(1)")]}
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


# ---------- 기본 dry-run과 --apply ----------


def test_default_run_is_a_dry_run_and_changes_nothing(world, invoke):
    before = snapshot(world)

    result = invoke(api_args(world), rows=[row(name="A"), row(code="9999", name="X")])

    assert result.code == EXIT_OK
    assert snapshot(world) == before
    assert not result.log_path.exists()  # 로그 파일도 만들지 않는다
    assert "[dry-run]" in result.out and "--apply" in result.out
    assert "dry-run이라 기록하지 않았습니다" in result.out


def test_dry_run_still_reports_what_would_change(world, invoke):
    result = invoke(api_args(world, "--json"), rows=[row(name="A"), row(name="B", use_yn="보수중")])

    body = result.json()
    assert body["dry_run"] is True and body["committed"] is False
    assert body["counts"]["elevators_created"] == 2 and body["counts"]["status_created"] == 2
    assert count(world, "elevators") == 0


def test_apply_writes_to_the_db_and_the_log(world, invoke):
    result = invoke(api_args(world, "--apply"), rows=[row(name="A"), row(code="9999", name="X")])

    assert result.code == EXIT_OK
    assert count(world, "elevators") == 1 and count(world, "elevator_status") == 1
    assert "DB에 반영했습니다" in result.out
    assert [(e["reason"], e["elevator_name"]) for e in result.log_lines] == [("NO_STATION_MATCH", "X")]


def test_apply_records_fetched_at_right_after_collection(world, invoke):
    result = invoke(api_args(world, "--apply"), rows=[row()], clock=CLOCK_T2)

    with world.connect() as conn:
        assert conn.execute(text("SELECT fetched_at, observed_at FROM elevator_status")).all() == [(T2, None)]
    assert "상태 관측 시각이 아닙니다" in result.out


def test_fetched_at_is_taken_after_the_fetch_not_before(world, invoke):
    order = []

    def clock():
        order.append("clock")
        return CLOCK_T1()

    result = invoke(api_args(world, "--apply"), rows=[row()], clock=clock, on_fetch=lambda: order.append("fetch"))

    assert result.code == EXIT_OK
    assert order[:2] == ["fetch", "clock"]  # 응답을 모두 받은 뒤에 시각을 찍는다


def test_api_client_receives_the_key_and_the_https_address(world, invoke):
    result = invoke(api_args(world), rows=[row()])

    assert result.calls == [(KEY, HTTPS)]
    assert result.client.closed is True


# ---------- 종료 코드 1: 데이터·적재 오류 ----------


def test_empty_ev_input_is_an_error(world, invoke):
    before = snapshot(world)

    only_escalators = invoke(api_args(world, "--apply"), rows=[row(se="ES"), row(name="B", se="WL")])
    nothing = invoke(api_args(world, "--apply"), rows=[])

    assert only_escalators.code == EXIT_DATA_ERROR and nothing.code == EXIT_DATA_ERROR
    assert "EV 설비가 0건" in only_escalators.err
    assert snapshot(world) == before


def test_malformed_rows_are_a_data_error(world, invoke):
    broken = row()
    del broken["USE_YN"]

    result = invoke(api_args(world, "--apply"), rows=[broken])

    assert result.code == EXIT_DATA_ERROR and "USE_YN" in result.err
    assert count(world, "elevators") == 0


def test_all_unmapped_fails_and_stores_nothing(world, invoke):
    result = invoke(api_args(world, "--apply", "--json"), rows=[row(code="9998", name="X"), row(code="9999", name="Y")])

    assert result.code == EXIT_DATA_ERROR
    body = result.json()
    assert body["committed"] is False and body["error"]["kind"] == "data"
    assert count(world, "elevators") == 0 and count(world, "elevator_status") == 0
    assert {e["reason"] for e in result.log_lines} == {"NO_STATION_MATCH"}  # 진단용 로그는 남긴다
    assert len(result.log_lines) == 2


def test_all_unmapped_also_fails_in_dry_run_without_a_log(world, invoke):
    result = invoke(api_args(world), rows=[row(code="9999")])

    assert result.code == EXIT_DATA_ERROR
    assert not result.log_path.exists()


def prepare_existing_state(world, invoke):
    """먼저 정상 적재해 기존 설비와 상태(fetched_at=T1)를 만든다."""
    first = invoke(api_args(world, "--apply"), rows=[row(name="기존 설비")], clock=CLOCK_T1)
    assert first.code == EXIT_OK
    return snapshot(world)


@pytest.mark.parametrize("apply_flags", [["--apply"], []], ids=["apply", "dry-run"])
def test_all_unmapped_never_commits_and_never_calls_the_upsert(world, invoke, monkeypatch, apply_flags):
    before = prepare_existing_state(world, invoke)
    recorder = EngineRecorder()
    monkeypatch.setattr(cli, "make_engine", recorder)
    upserts = []
    monkeypatch.setattr(cli, "ingest_elevators", lambda *a, **k: upserts.append(1))

    result = invoke(api_args(world, *apply_flags, "--json"), rows=[row(code="9998", name="X"), row(code="9999", name="Y")], clock=CLOCK_T2)

    assert result.code == EXIT_DATA_ERROR
    assert recorder.created == 1 and recorder.commits == 0  # 커밋이 한 번도 일어나지 않았다
    assert recorder.writes == []  # INSERT/UPDATE/DELETE도 없었다
    assert upserts == []  # 적재 함수를 부르지 않았다
    assert snapshot(world) == before  # 기존 DB 그대로
    with world.connect() as conn:
        assert conn.execute(text("SELECT fetched_at FROM elevator_status")).all() == [(T1,)]  # fetched_at 유지
    body = result.json()
    assert body["committed"] is False and body["counts"]["unmapped"] == 2 and body["unmapped_station_codes"] == ["9998", "9999"]
    assert body["counts"]["elevators_created"] == 0 and body["counts"]["status_updated"] == 0


def test_all_unmapped_apply_keeps_the_diagnostic_log_but_dry_run_does_not(world, invoke):
    rows = [row(code="9998", name="X", use_yn="고장"), row(code="9999", name="Y")]

    dry = invoke(api_args(world), rows=rows, run_id="run-dry")
    applied = invoke(api_args(world, "--apply"), rows=rows, run_id="run-apply")

    assert dry.code == applied.code == EXIT_DATA_ERROR
    assert [(e["run_id"], e["reason"], e["external_station_code"]) for e in applied.log_lines] == [
        ("run-apply", "NO_STATION_MATCH", "9998"), ("run-apply", "NO_STATION_MATCH", "9999"),
    ]  # dry-run은 기록하지 않았고, --apply만 남겼다
    assert applied.log_lines[0]["source_status_text"] == "고장"


def test_all_unmapped_log_failure_does_not_hide_the_main_failure(world, invoke, monkeypatch):
    monkeypatch.setattr(cli, "append_entries", lambda p, e: (_ for _ in ()).throw(IngestionLogError("로그를 기록하지 못했습니다 (OSError).")))

    result = invoke(api_args(world, "--apply"), rows=[row(code="9999")])

    assert result.code == EXIT_DATA_ERROR  # 종료 코드 4가 아니라 원래 실패(1)다. DB는 커밋된 것이 없다.
    assert "미매핑 로그도 기록하지 못했습니다" in result.err
    assert count(world, "elevators") == 0


def test_safety_net_still_fails_when_mapping_disappears_between_check_and_upsert(world, invoke, monkeypatch):
    before = prepare_existing_state(world, invoke)
    monkeypatch.setattr(cli, "_mapped_station_codes", lambda engine, codes: {"0150"})  # 사전 확인은 통과했다고 가정

    result = invoke(api_args(world, "--apply"), rows=[row(code="9999", name="X")], clock=CLOCK_T2)

    assert result.code == EXIT_DATA_ERROR  # 적재 후 검사가 실패로 처리한다
    assert snapshot(world) == before
    assert [e["reason"] for e in result.log_lines if e["external_station_code"] == "9999"] == ["NO_STATION_MATCH"]


def test_some_mapped_still_commits_and_logs_the_unmapped(world, invoke, monkeypatch):
    recorder = EngineRecorder()
    monkeypatch.setattr(cli, "make_engine", recorder)

    result = invoke(api_args(world, "--apply"), rows=[row(name="A"), row(code="9999", name="X")])

    assert result.code == EXIT_OK and recorder.commits >= 1 and recorder.writes  # 일부만 미매핑이면 정상 적재한다
    assert count(world, "elevators") == 1


def test_partial_unmapped_warns_stores_the_rest_and_logs_the_others(world, invoke):
    rows = [row(name="A"), row(code="9999", name="X", station="가상역"), row(code="9998", name="Y")]

    result = invoke(api_args(world, "--apply"), rows=rows)

    assert result.code == EXIT_OK
    assert "경고" in result.err and "2건" in result.err
    assert count(world, "elevators") == 1
    assert {(e["external_station_code"], e["reason"]) for e in result.log_lines} == {
        ("9999", "NO_STATION_MATCH"), ("9998", "NO_STATION_MATCH"),
    }
    assert any(e["station_name_raw"] == "가상역" for e in result.log_lines)


def test_unknown_status_is_stored_as_elevator_only_and_logged_with_original_text(world, invoke):
    result = invoke(api_args(world, "--apply"), rows=[row(name="U", use_yn="점검 중"), row(name="K")])

    assert result.code == EXIT_OK
    assert count(world, "elevators") == 2 and count(world, "elevator_status") == 1  # 미확인은 상태 행이 없다
    unknown = [e for e in result.log_lines if e["reason"] == "UNKNOWN_STATUS"]
    assert [(e["elevator_name"], e["source_status_text"]) for e in unknown] == [("U", "점검 중")]
    assert "상태를 해석하지 못한 설비 1건" in result.err


def test_mapping_conflict_rolls_back_and_exits_1(world, invoke):
    invoke(api_args(world, "--apply"), rows=[row(code="0150", name="기존")])
    with world.begin() as conn:
        conn.execute(text("UPDATE station_external_ids SET station_id = 2 WHERE external_station_code = '0150'"))
    before = snapshot(world)

    result = invoke(api_args(world, "--apply"), rows=[row(code="0151", name="새 설비"), row(code="0150", name="기존", use_yn="보수중")], clock=CLOCK_T2)

    assert result.code == EXIT_DATA_ERROR and "충돌" in result.err
    assert snapshot(world) == before  # 같은 입력의 정상 건도 남지 않았다


def test_older_fetched_at_cannot_overwrite_a_newer_state(world, invoke):
    invoke(api_args(world, "--apply"), rows=[row()], clock=CLOCK_T2)
    before = snapshot(world)

    result = invoke(api_args(world, "--apply"), rows=[row(use_yn="보수중")], clock=CLOCK_T1)

    assert result.code == EXIT_DATA_ERROR and "과거" in result.err
    assert snapshot(world) == before


def test_missing_existing_elevators_are_neither_deleted_nor_changed(world, invoke):
    invoke(api_args(world, "--apply"), rows=[row(name="남는 설비"), row(name="빠질 설비", use_yn="보수중")])
    before = [r for r in snapshot(world)["elevators"] if r[4] == "빠질 설비"]

    result = invoke(api_args(world, "--apply"), rows=[row(name="남는 설비")], clock=CLOCK_T2)

    assert result.code == EXIT_OK
    assert [r for r in snapshot(world)["elevators"] if r[4] == "빠질 설비"] == before
    with world.connect() as conn:
        kept = conn.execute(text("SELECT status, fetched_at FROM elevator_status WHERE status = '가동 안됨'")).all()
    assert kept == [("가동 안됨", T1)]  # 빠진 설비의 상태와 fetched_at은 그대로


def test_locked_database_is_reported_and_rolled_back(world, invoke, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "make_engine", lambda url: create_engine(url, connect_args={"timeout": 0.2}))
    path = world.url.database
    holder = sqlite3.connect(path, timeout=0.2, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")  # 다른 프로세스가 쓰기 잠금을 쥔 상황
    try:
        result = invoke(api_args(world, "--apply"), rows=[row()])
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    assert result.code == EXIT_DATA_ERROR and "잠겨" in result.err
    assert count(world, "elevators") == 0
    assert not result.log_lines  # 적재가 실패했으므로 로그에도 남기지 않는다


# ---------- 종료 코드 2: 실행 준비 오류 ----------


def test_missing_key_is_not_ready(world, invoke):
    result = invoke(api_args(world), rows=[row()], key="")

    assert result.code == EXIT_NOT_READY and "API 키" in result.err
    assert result.calls == []


def test_api_mode_requires_an_explicit_https_address(world, invoke):
    result = invoke(["--database-url", db_url(world)], rows=[row()])

    assert result.code == EXIT_NOT_READY and "HTTPS" in result.err
    assert result.calls == [] and result.client.fetched == 0


@pytest.mark.parametrize(
    "base_url",
    ["http://mock.test", "http://openapi.seoul.go.kr:8088", "ftp://mock.test", "mock.test", "https://", "https://user:pw@mock.test", "https://mock.test?x=1", "https://mock.test#f"],
)
def test_plain_http_and_malformed_addresses_are_rejected_before_any_call(world, invoke, base_url):
    result = invoke(["--base-url", base_url, "--database-url", db_url(world), "--apply"], rows=[row()])

    assert result.code == EXIT_NOT_READY
    assert result.calls == [] and result.client.fetched == 0
    assert "pw" not in result.blob  # 주소에 든 정보를 되풀이해 출력하지 않는다


def test_cli_does_not_expose_an_insecure_http_option(world, invoke):
    result = invoke(["--allow-insecure-http", "--base-url", "http://mock.test", "--database-url", db_url(world)], rows=[row()])

    assert result.code == EXIT_NOT_READY and "unrecognized" in result.err
    assert result.calls == []


def test_help_lists_no_insecure_option(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--help"])

    assert info.value.code == 0
    assert "insecure" not in capsys.readouterr().out.lower()


def test_client_config_error_is_not_ready(world, invoke):
    result = invoke(api_args(world), error=SeoulOpenApiConfigError("설정 오류"))

    assert result.code == EXIT_NOT_READY


def test_file_mode_requires_fetched_at(world, invoke, sample_file):
    result = invoke(["--input-file", str(sample_file), "--database-url", db_url(world)])

    assert result.code == EXIT_NOT_READY and "--fetched-at" in result.err
    assert count(world, "elevators") == 0


@pytest.mark.parametrize("bad", ["", "2030-01-01", "2030-01-01T03:00:00", "2030-01-01T12:00:00+09:00", "2030-01-01T03:00:00+00:00", "2030-01-01T03:00:00.5Z"])
def test_file_mode_rejects_invalid_fetched_at(world, invoke, sample_file, bad):
    result = invoke(file_args(world, sample_file, "--apply", fetched_at=bad))

    assert result.code == EXIT_NOT_READY
    assert count(world, "elevators") == 0


def test_fetched_at_is_not_allowed_in_api_mode(world, invoke):
    result = invoke(api_args(world, "--fetched-at", T1), rows=[row()])

    assert result.code == EXIT_NOT_READY and result.calls == []


def test_base_url_is_not_allowed_in_file_mode(world, invoke, sample_file):
    result = invoke(file_args(world, sample_file, "--base-url", HTTPS))

    assert result.code == EXIT_NOT_READY


def test_missing_input_file_is_not_ready(world, invoke, tmp_path):
    result = invoke(file_args(world, tmp_path / "nope.json"))

    assert result.code == EXIT_NOT_READY


def test_unmigrated_database_is_not_ready(alembic_cfg_for, migrated_url, invoke):
    command.upgrade(alembic_cfg_for(migrated_url), "8d70068f68f4")  # station_line_id 이전 상태

    result = invoke(["--base-url", HTTPS, "--database-url", migrated_url, "--apply"], rows=[row()])

    assert result.code == EXIT_NOT_READY and "마이그레이션" in result.err
    assert result.calls == []  # DB 준비 확인이 외부 수집보다 먼저다


def test_missing_sqlite_file_is_not_ready_and_not_created(invoke, tmp_path):
    missing = tmp_path / "missing.db"

    result = invoke(["--base-url", HTTPS, "--database-url", f"sqlite:///{missing}"], rows=[row()])

    assert result.code == EXIT_NOT_READY and not missing.exists()


def test_log_directory_problem_is_caught_before_commit(world, invoke, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    before = snapshot(world)

    result = invoke(api_args(world, "--apply"), rows=[row()], log_path=blocker / "logs" / "issues.jsonl")

    assert result.code == EXIT_NOT_READY and "로그" in result.err
    assert result.calls == [] and snapshot(world) == before  # 수집도 커밋도 하지 않았다


def test_dry_run_does_not_check_or_create_the_log_directory(world, invoke, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")

    result = invoke(api_args(world), rows=[row()], log_path=blocker / "logs" / "issues.jsonl")

    assert result.code == EXIT_OK  # dry-run은 로그를 쓰지 않으므로 경로를 보지 않는다


# ---------- 종료 코드 3: 외부 API 오류 ----------


@pytest.mark.parametrize(
    "error",
    [SeoulOpenApiTransportError("요청 시간 초과 (범위 1-1000)"), SeoulOpenApiResponseError("오류 응답 ERROR-336: 범위 초과", code="ERROR-336")],
)
def test_api_errors_keep_the_db_and_fetched_at(world, invoke, error):
    invoke(api_args(world, "--apply"), rows=[row()], clock=CLOCK_T1)
    before = snapshot(world)
    log_before = (world.url.database, )

    result = invoke(api_args(world, "--apply", "--json"), error=error, clock=CLOCK_T2)

    assert result.code == EXIT_API_ERROR
    assert result.json()["error"]["kind"] == "api" and result.json()["committed"] is False
    assert snapshot(world) == before
    with world.connect() as conn:
        assert conn.execute(text("SELECT fetched_at FROM elevator_status")).all() == [(T1,)]  # fetched_at 유지
    assert log_before


def test_api_error_writes_no_log_entries(world, invoke):
    result = invoke(api_args(world, "--apply"), error=SeoulOpenApiTransportError("네트워크 오류 ConnectError (범위 1-1000)"))

    assert result.code == EXIT_API_ERROR
    assert result.log_lines == []


# ---------- 종료 코드 4: 커밋 후 로그 기록 실패 ----------


def test_log_failure_after_commit_is_a_partial_success(world, invoke, monkeypatch):
    def failing(path, entries):
        raise IngestionLogError("로그를 기록하지 못했습니다 (OSError).")

    monkeypatch.setattr(cli, "append_entries", failing)

    result = invoke(api_args(world, "--apply", "--json"), rows=[row(name="A"), row(code="9999", name="X")])

    assert result.code == EXIT_LOG_FAILED
    body = result.json()
    assert body["ok"] is False and body["partial_success"] is True
    assert body["committed"] is True and body["log"]["written"] is False
    assert body["error"]["kind"] == "log"
    assert "커밋" in body["error"]["message"] and "롤백되지 않았습니다" in body["error"]["message"]
    assert count(world, "elevators") == 1  # 커밋된 DB는 그대로 유지된다


def test_log_failure_text_output_does_not_claim_success_or_rollback(world, invoke, monkeypatch):
    monkeypatch.setattr(cli, "append_entries", lambda p, e: (_ for _ in ()).throw(IngestionLogError("로그를 기록하지 못했습니다 (OSError).")))

    result = invoke(api_args(world, "--apply"), rows=[row()])

    assert result.code == EXIT_LOG_FAILED
    assert "[부분 성공]" in result.out and "롤백되지 않았습니다" in result.out
    assert "롤백했" not in result.out + result.err
    assert "같은 명령을 다시 실행" in result.err


def test_rerunning_after_a_log_failure_keeps_the_db_and_writes_the_log(world, invoke, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(cli, "append_entries", lambda p, e: (_ for _ in ()).throw(IngestionLogError("로그를 기록하지 못했습니다 (OSError).")))
        first = invoke(api_args(world, "--apply"), rows=[row(code="9999", name="X"), row(name="A")])
    after_first = snapshot(world)

    second = invoke(api_args(world, "--apply"), rows=[row(code="9999", name="X"), row(name="A")], run_id="run-2")

    assert first.code == EXIT_LOG_FAILED and second.code == EXIT_OK
    assert snapshot(world) == after_first  # 같은 fetched_at이면 DB는 그대로
    assert [e["run_id"] for e in second.log_lines] == ["run-2"]


# ---------- JSONL: append와 run_id ----------


def test_log_is_appended_across_runs_and_runs_are_told_apart(world, invoke):
    first = invoke(api_args(world, "--apply"), rows=[row(name="A"), row(code="9999", name="X")], run_id="run-A")
    before = first.log_path.read_text(encoding="utf-8")

    second = invoke(api_args(world, "--apply"), rows=[row(name="A"), row(code="9998", name="Y")], run_id="run-B")

    text_after = second.log_path.read_text(encoding="utf-8")
    assert text_after.startswith(before)  # 덮어쓰지 않는다
    assert [(e["run_id"], e["external_station_code"]) for e in second.log_lines] == [("run-A", "9999"), ("run-B", "9998")]


def test_log_entries_have_the_documented_fields(world, invoke):
    result = invoke(api_args(world, "--apply"), rows=[row(code="9999", name="X"), row(name="U", use_yn="고장")])

    assert result.log_lines
    for entry in result.log_lines:
        assert set(entry) == {
            "run_id", "logged_at", "source", "reason", "external_station_code",
            "station_name_raw", "elevator_name", "source_status_text",
        }
        assert entry["source"] == "OA-15994" and entry["logged_at"] == T1


def test_new_run_id_is_used_by_default(world, capsys, tmp_path):
    path = tmp_path / "logs" / "issues.jsonl"
    args = api_args(world, "--apply")
    factory = lambda k, u: FakeClient(rows=[row(code="9999", name="X"), row(name="A")])  # noqa: E731

    main(args, client_factory=factory, clock=CLOCK_T1, log_path=path, api_key=KEY)
    main(args, client_factory=factory, clock=CLOCK_T1, log_path=path, api_key=KEY)
    capsys.readouterr()

    run_ids = [json.loads(line)["run_id"] for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(run_ids) == 2 and run_ids[0] != run_ids[1]


# ---------- 파일 모드와 DB 위치 차단 ----------


@pytest.fixture
def seeded(engine):
    rows, links, review = read_seed_dir(SEED)
    with Session(engine) as session:
        load_seed(session, rows, links, review)
    return engine


def test_file_mode_loads_the_sample_into_a_temporary_db(seeded, invoke):
    result = invoke(file_args(seeded, SAMPLE, "--apply", "--json", fetched_at=T1))

    assert result.code == EXIT_OK
    body = result.json()
    assert body["mode"] == "file" and body["fetched_at"] == T1
    assert body["counts"]["ev_elevators"] == 866 and body["counts"]["elevators_created"] == 866
    assert body["counts"]["unmapped"] == 0 and body["unmapped_station_codes"] == []
    assert count(seeded, "elevators") == 866 and count(seeded, "elevator_status") == 866
    assert result.calls == []  # 파일 모드는 클라이언트를 만들지 않는다


def test_file_mode_dry_run_leaves_the_db_unchanged(seeded, invoke):
    before = snapshot(seeded)

    result = invoke(file_args(seeded, SAMPLE))

    assert result.code == EXIT_OK and snapshot(seeded) == before


def test_file_mode_accepts_a_saved_raw_response(world, invoke, tmp_path):
    raw = {"SeoulMetroFaciInfo": {"list_total_count": 2, "RESULT": {"CODE": "INFO-000", "MESSAGE": "ok"}, "row": [row(name="A"), row(name="B")]}}
    path = tmp_path / "raw.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    result = invoke(file_args(world, path, "--apply"))

    assert result.code == EXIT_OK and count(world, "elevators") == 2


def test_file_mode_rejects_a_partial_raw_response(world, invoke, tmp_path):
    raw = {"SeoulMetroFaciInfo": {"list_total_count": 5, "RESULT": {"CODE": "INFO-000", "MESSAGE": "ok"}, "row": [row()]}}
    path = tmp_path / "raw.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    result = invoke(file_args(world, path, "--apply"))

    assert result.code == EXIT_DATA_ERROR and "일부 페이지" in result.err
    assert count(world, "elevators") == 0


@pytest.mark.parametrize("content", ["not json", "[]", "{}", '{"x": 1}'])
def test_file_mode_rejects_unknown_formats(world, invoke, tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")

    result = invoke(file_args(world, path, "--apply"))

    assert result.code == EXIT_DATA_ERROR
    assert count(world, "elevators") == 0


class DbSpy:
    """make_engine 호출을 기록하고 막는다. 차단 검사가 DB 엔진을 만들기 전에 일어나는지 확인한다."""

    def __init__(self):
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        raise RuntimeError("DB가 열렸습니다")


class EngineRecorder:
    """실제 엔진을 만들되 커밋과 쓰기 문(INSERT/UPDATE/DELETE)을 기록한다."""

    def __init__(self):
        self.commits = 0
        self.writes: list[str] = []
        self.created = 0

    def __call__(self, url):
        self.created += 1
        engine = real_make_engine(url)
        event.listen(engine, "commit", lambda conn: setattr(self, "commits", self.commits + 1))

        def record(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().split(None, 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
                self.writes.append(statement.split("(")[0][:60])

        event.listen(engine, "before_cursor_execute", record)
        return engine


BOTH_MODES = pytest.mark.parametrize("apply_flags", [[], ["--apply"]], ids=["dry-run", "apply"])


def file_cli(invoke, sample_file, apply_flags, url=None):
    args = ["--input-file", str(sample_file), "--fetched-at", T1, *apply_flags]
    if url is not None:
        args += ["--database-url", url]
    return invoke(args)


@BOTH_MODES
def test_file_mode_rejects_the_default_db(world, invoke, monkeypatch, sample_file, apply_flags):
    monkeypatch.setattr(cli.settings, "database_url", db_url(world))
    spy = DbSpy()
    monkeypatch.setattr(cli, "make_engine", spy)

    result = file_cli(invoke, sample_file, apply_flags)  # --database-url 생략 = 기본 DB

    assert result.code == EXIT_NOT_READY and "기본 DB" in result.err
    assert spy.calls == []  # DB 엔진을 만들기 전에 막혔다


@BOTH_MODES
def test_file_mode_rejects_the_default_db_given_explicitly(world, invoke, monkeypatch, sample_file, apply_flags):
    monkeypatch.setattr(cli.settings, "database_url", db_url(world))
    spy = DbSpy()
    monkeypatch.setattr(cli, "make_engine", spy)

    result = file_cli(invoke, sample_file, apply_flags, db_url(world))

    assert result.code == EXIT_NOT_READY and spy.calls == []


@BOTH_MODES
def test_file_mode_rejects_dbs_under_the_repository_data_directory(world, invoke, monkeypatch, tmp_path, sample_file, apply_flags):
    fake_data = tmp_path / "data"
    fake_data.mkdir()
    monkeypatch.setattr(cli, "PROJECT_DATA_DIR", fake_data)
    spy = DbSpy()
    monkeypatch.setattr(cli, "make_engine", spy)

    result = file_cli(invoke, sample_file, apply_flags, f"sqlite:///{fake_data / 'other.db'}")

    assert result.code == EXIT_NOT_READY and "data/" in result.err
    assert spy.calls == []


@BOTH_MODES
def test_file_mode_rejects_the_real_repository_dev_db_without_opening_it(invoke, monkeypatch, sample_file, apply_flags):
    spy = DbSpy()
    monkeypatch.setattr(cli, "make_engine", spy)

    absolute = file_cli(invoke, sample_file, apply_flags, f"sqlite:///{ROOT / 'data' / 'dev.db'}")
    monkeypatch.chdir(ROOT)
    relative = file_cli(invoke, sample_file, apply_flags, "sqlite:///data/dev.db")
    dotted = file_cli(invoke, sample_file, apply_flags, "sqlite:///./app/../data/dev.db")

    assert absolute.code == relative.code == dotted.code == EXIT_NOT_READY
    assert spy.calls == []  # 실제 dev.db는 dry-run에서도 열리지 않았다


@BOTH_MODES
def test_relative_paths_are_compared_by_real_location(world, invoke, monkeypatch, sample_file, apply_flags):
    default_file = Path(world.url.database)
    monkeypatch.chdir(default_file.parent)
    monkeypatch.setattr(cli.settings, "database_url", f"sqlite:///./{default_file.name}")
    spy = DbSpy()
    monkeypatch.setattr(cli, "make_engine", spy)

    result = file_cli(invoke, sample_file, apply_flags, f"sqlite:///./nested/../{default_file.name}")

    assert result.code == EXIT_NOT_READY and "기본 DB" in result.err and spy.calls == []


@BOTH_MODES
def test_symlink_to_the_default_db_is_rejected(world, invoke, monkeypatch, tmp_path, sample_file, apply_flags):
    link = tmp_path / "innocent.db"
    link.symlink_to(world.url.database)
    monkeypatch.setattr(cli.settings, "database_url", db_url(world))
    spy = DbSpy()
    monkeypatch.setattr(cli, "make_engine", spy)

    result = file_cli(invoke, sample_file, apply_flags, f"sqlite:///{link}")

    assert result.code == EXIT_NOT_READY and "기본 DB" in result.err and spy.calls == []


@BOTH_MODES
def test_symlink_chain_to_the_default_db_is_rejected(world, invoke, monkeypatch, tmp_path, sample_file, apply_flags):
    first = tmp_path / "first.db"
    first.symlink_to(world.url.database)
    second = tmp_path / "second.db"
    second.symlink_to(first)
    monkeypatch.setattr(cli.settings, "database_url", db_url(world))
    spy = DbSpy()
    monkeypatch.setattr(cli, "make_engine", spy)

    result = file_cli(invoke, sample_file, apply_flags, f"sqlite:///{second}")

    assert result.code == EXIT_NOT_READY and spy.calls == []


@BOTH_MODES
def test_symlinked_directory_into_the_data_directory_is_rejected(world, invoke, monkeypatch, tmp_path, sample_file, apply_flags):
    fake_data = tmp_path / "data"
    fake_data.mkdir()
    link_dir = tmp_path / "shortcut"
    link_dir.symlink_to(fake_data, target_is_directory=True)
    monkeypatch.setattr(cli, "PROJECT_DATA_DIR", fake_data)
    spy = DbSpy()
    monkeypatch.setattr(cli, "make_engine", spy)

    result = file_cli(invoke, sample_file, apply_flags, f"sqlite:///{link_dir / 'x.db'}")

    assert result.code == EXIT_NOT_READY and "data/" in result.err and spy.calls == []


@BOTH_MODES
def test_hard_link_to_the_default_db_is_rejected(world, invoke, monkeypatch, tmp_path, sample_file, apply_flags):
    hard = tmp_path / "hardlink.db"
    os.link(world.url.database, hard)  # 같은 파일의 또 다른 이름
    monkeypatch.setattr(cli.settings, "database_url", db_url(world))
    spy = DbSpy()
    monkeypatch.setattr(cli, "make_engine", spy)

    result = file_cli(invoke, sample_file, apply_flags, f"sqlite:///{hard}")

    assert result.code == EXIT_NOT_READY and "기본 DB" in result.err and spy.calls == []


@BOTH_MODES
def test_case_variant_of_the_default_db_path_is_rejected(world, invoke, monkeypatch, sample_file, apply_flags):
    default_file = Path(world.url.database)
    variant = default_file.parent / default_file.name.swapcase()
    if not variant.exists():
        pytest.skip("대소문자를 구분하는 파일 시스템이라 같은 파일의 대소문자 표기가 존재하지 않는다")
    monkeypatch.setattr(cli.settings, "database_url", db_url(world))
    spy = DbSpy()
    monkeypatch.setattr(cli, "make_engine", spy)

    result = file_cli(invoke, sample_file, apply_flags, f"sqlite:///{variant}")

    assert result.code == EXIT_NOT_READY and spy.calls == []


@BOTH_MODES
def test_url_query_parameters_do_not_hide_the_default_db(world, invoke, monkeypatch, sample_file, apply_flags):
    monkeypatch.setattr(cli.settings, "database_url", db_url(world))
    spy = DbSpy()
    monkeypatch.setattr(cli, "make_engine", spy)

    result = file_cli(invoke, sample_file, apply_flags, db_url(world) + "?check_same_thread=false")

    assert result.code == EXIT_NOT_READY and spy.calls == []


@BOTH_MODES
def test_file_mode_rejects_dbs_outside_a_temporary_directory(invoke, monkeypatch, sample_file, apply_flags):
    spy = DbSpy()
    monkeypatch.setattr(cli, "make_engine", spy)

    result = file_cli(invoke, sample_file, apply_flags, f"sqlite:///{ROOT / 'not-a-temp-dir.db'}")

    assert result.code == EXIT_NOT_READY and "임시 디렉터리" in result.err and spy.calls == []


@BOTH_MODES
@pytest.mark.parametrize("url", ["postgresql://u:p@localhost/db", "sqlite://", "sqlite:///:memory:", "sqlite:///file:x.db?mode=memory&uri=true"])
def test_file_mode_rejects_non_file_databases(invoke, monkeypatch, sample_file, apply_flags, url):
    spy = DbSpy()
    monkeypatch.setattr(cli, "make_engine", spy)

    result = file_cli(invoke, sample_file, apply_flags, url)

    assert result.code == EXIT_NOT_READY and spy.calls == []
    assert "p@localhost" not in result.blob  # 접속 정보를 출력하지 않는다


def test_file_mode_allows_a_temporary_sqlite_db_for_dry_run_and_apply(world, invoke, sample_file):
    before = snapshot(world)

    dry = file_cli(invoke, sample_file, [], db_url(world))
    after_dry = snapshot(world)
    applied = file_cli(invoke, sample_file, ["--apply"], db_url(world))

    assert dry.code == EXIT_OK and after_dry == before  # dry-run은 DB를 바꾸지 않는다
    assert applied.code == EXIT_OK and count(world, "elevators") == 3  # --apply는 반영한다


def test_the_dry_run_guard_runs_before_any_other_database_access(world, invoke, monkeypatch, sample_file):
    """DB 엔진 생성뿐 아니라 준비 상태 확인(스키마 조회)도 차단 뒤에야 일어난다."""
    monkeypatch.setattr(cli.settings, "database_url", db_url(world))
    inspected = []
    monkeypatch.setattr(cli, "_check_database_ready", lambda engine, url: inspected.append(url))
    monkeypatch.setattr(cli, "make_engine", DbSpy())

    result = file_cli(invoke, sample_file, [])

    assert result.code == EXIT_NOT_READY and inspected == []


def test_api_mode_may_apply_to_any_database(world, invoke, monkeypatch):
    monkeypatch.setattr(cli.settings, "database_url", db_url(world))

    result = invoke(["--base-url", HTTPS, "--apply"], rows=[row()])  # 기본 DB를 가리켜도 API 모드는 허용된다

    assert result.code == EXIT_OK and count(world, "elevators") == 1


# ---------- 외부 수집은 DB 쓰기 트랜잭션 전에 ----------


def test_fetch_finishes_before_any_write_transaction_is_opened(world, invoke):
    probe = create_engine(db_url(world), connect_args={"timeout": 0.2})
    state = {}

    def during_fetch():
        try:
            with probe.begin() as conn:  # 다른 연결이 쓰기를 시도한다. CLI가 잠금을 쥐고 있으면 실패한다.
                conn.execute(text("INSERT INTO stations (station_id, station_name) VALUES (500, '수집 중 쓰기')"))
            state["write_during_fetch"] = True
        except Exception as exc:  # noqa: BLE001
            state["write_during_fetch"] = repr(exc)

    result = invoke(api_args(world, "--apply"), rows=[row()], on_fetch=during_fetch)
    probe.dispose()

    assert state["write_during_fetch"] is True  # 수집 중에는 DB 잠금이 없었다
    assert result.code == EXIT_OK


# ---------- --json 출력 ----------


def test_json_output_has_a_stable_structure_on_success(world, invoke):
    result = invoke(api_args(world, "--apply", "--json"), rows=[row(name="A"), row(se="ES", name="E")])

    body = result.json()
    assert set(body) == JSON_KEYS
    assert set(body["counts"]) == COUNT_KEYS
    assert set(body["log"]) == {"path", "enabled", "entries", "written"}
    assert body["schema_version"] == 1 and body["ok"] is True and body["partial_success"] is False
    assert body["exit_code"] == 0 and body["run_id"] == "run-1" and body["error"] is None
    assert body["counts"]["excluded_by_type"] == {"ES": 1} and body["counts"]["ev_elevators"] == 1
    assert result.err == ""  # --json에서 경고가 없으면 stderr는 비어 있다


@pytest.mark.parametrize(
    "args_rows_error, kind",
    [
        (("api", [], None), "data"),  # EV 0건
        (("api", None, SeoulOpenApiTransportError("요청 시간 초과 (범위 1-1000)")), "api"),
    ],
)
def test_json_output_has_the_same_keys_on_failure(world, invoke, args_rows_error, kind):
    _, rows, error = args_rows_error

    result = invoke(api_args(world, "--apply", "--json"), rows=rows, error=error)

    body = result.json()
    assert set(body) == JSON_KEYS and set(body["counts"]) == COUNT_KEYS
    assert body["ok"] is False and body["error"]["kind"] == kind and body["exit_code"] != 0


def test_json_output_for_usage_errors_is_still_one_json_object(world, invoke):
    result = invoke(["--json", "--database-url", db_url(world)], rows=[row()])  # HTTPS 주소 없음

    body = result.json()
    assert set(body) == JSON_KEYS and body["exit_code"] == EXIT_NOT_READY and body["error"]["kind"] == "not_ready"


def test_json_dry_run_marks_the_log_as_disabled(world, invoke):
    body = invoke(api_args(world, "--json"), rows=[row()]).json()

    assert body["dry_run"] is True and body["log"] == {"path": None, "enabled": False, "entries": 0, "written": None}


# ---------- 키와 URL 비노출 ----------


def test_key_and_url_never_appear_in_any_output(world, invoke):
    leaky_station = f"역 {KEY} http://mock.test/{KEY}/json/S/1/5/"
    scenarios = [
        dict(args=api_args(world, "--apply"), rows=[row(name="A"), row(code="9999", name=f"설비 {KEY}", station=leaky_station)]),
        dict(args=api_args(world, "--apply", "--json"), rows=[row(name="U", use_yn=f"고장 {KEY}")]),
        dict(args=api_args(world, "--apply"), rows=[row(code="9999", station=leaky_station)]),  # 전부 미매핑
        dict(args=api_args(world, "--apply"), error=SeoulOpenApiTransportError(f"네트워크 오류: https://mock.test/{KEY}/json/S/1/5/ {KEY}")),
        dict(args=api_args(world, "--apply", "--json"), error=SeoulOpenApiResponseError(f"오류 응답 {KEY}", code=KEY)),
        dict(args=api_args(world), rows=[row()]),
        dict(args=["--base-url", "http://mock.test", "--database-url", db_url(world)], rows=[row()]),
    ]

    for scenario in scenarios:
        result = invoke(scenario["args"], rows=scenario.get("rows"), error=scenario.get("error"))
        assert KEY not in result.blob, scenario["args"]
        assert f"/{KEY}/" not in result.blob
        assert "mock.test" not in result.out + result.err  # 요청 주소를 출력하지 않는다


def test_unexpected_errors_show_only_the_exception_type(world, invoke):
    def boom():
        raise ValueError(f"내부 오류 https://mock.test/{KEY}/json/")

    result = invoke(api_args(world, "--apply"), rows=[row()], on_fetch=boom)

    assert result.code == EXIT_DATA_ERROR
    assert "ValueError" in result.err
    assert KEY not in result.blob and "mock.test" not in result.blob


# ---------- Alembic 로깅, 격리 ----------


def test_cli_output_does_not_depend_on_logging_configuration(world, invoke):
    # conftest의 engine 픽스처가 Alembic을 실행해 기존 로거를 꺼 둔 상태에서도 출력은 print로 나온다.
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level

    result = invoke(api_args(world), rows=[row()])

    assert result.code == EXIT_OK and "[dry-run]" in result.out
    assert list(root.handlers) == handlers and root.level == level  # 로깅 설정을 바꾸지 않는다


def test_the_cli_uses_a_dedicated_session_for_ingest(world, invoke, monkeypatch):
    sessions = []
    real = cli.ingest_elevators

    def spy(session, records, **kwargs):
        sessions.append(session)
        assert not session.new and not session.dirty and not session.deleted  # 다른 작업이 없는 새 세션이다
        return real(session, records, **kwargs)

    monkeypatch.setattr(cli, "ingest_elevators", spy)

    invoke(api_args(world, "--apply"), rows=[row()])

    assert len(sessions) == 1


def test_no_database_write_before_the_input_is_validated(world, invoke):
    before = snapshot(world)

    result = invoke(api_args(world, "--apply"), rows=[row(se="ES")])  # EV 0건

    assert result.code == EXIT_DATA_ERROR and snapshot(world) == before
