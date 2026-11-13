"""OA-15994 엘리베이터 수집 CLI.

    # 아래 <확인된 HTTPS 주소>는 자리표시자이며 실제 주소가 아니다. 확인된 HTTPS 주소는 아직 없다.
    # 기본은 dry-run이다. DB와 로그 파일을 바꾸지 않는다.
    uv run python -m app.cli.collect_elevators --base-url https://<확인된 HTTPS 주소>
    # 실제 반영은 --apply를 명시했을 때만 한다.
    uv run python -m app.cli.collect_elevators --base-url https://<확인된 HTTPS 주소> --apply
    # 저장된 파일로 임시 DB에 적재해 보기 (파일 모드는 --fetched-at이 필수다)
    uv run python -m app.cli.collect_elevators --input-file <파일.json> --fetched-at 2030-01-01T03:00:00Z \\
        --database-url sqlite:////tmp/test.db --apply

종료 코드
    0 정상
    1 데이터·적재 오류 (파싱 오류, EV 0건, 전부 미매핑, 매핑 충돌, 과거 fetched_at, DB 잠금 등)
    2 실행 준비 오류 (인자, 키 없음, HTTPS 주소 없음, DB 미준비, 로그 경로 쓰기 불가, 차단된 조합)
    3 외부 API 오류 (전송, 응답 오류). 기존 DB와 fetched_at은 그대로다.
    4 DB 커밋은 성공했으나 로그 기록에 실패 (부분 성공). 커밋된 DB는 롤백되지 않았다.

안전 규칙
- API 모드는 HTTPS 주소(--base-url https://...)가 있어야만 실행한다. 평문 HTTP는 허용하지 않으며
  이를 풀어 주는 옵션을 노출하지 않는다.
- 파일 모드는 --apply 여부와 관계없이(dry-run 포함) 기본 DB와 저장소 data/ 아래 DB에 접근하지 않고,
  임시 디렉터리의 SQLite 파일에서만 동작한다. 경로는 상대 경로, 심볼릭 링크, 하드 링크를 풀어 같은
  파일인지로 검사한다. 이 검사는 DB 엔진을 만들기 전에 한다.
- 모든 외부 역 코드가 매핑되지 않았으면 적재 함수를 부르지 않고 종료 코드 1로 실패한다(커밋 없음).
- 외부 수집은 DB 쓰기 트랜잭션을 열기 전에 끝낸다. 쓰기 잠금은 적재하는 동안에만 잡는다.
- 적재는 이 명령 전용의 새 Session 하나로만 한다.
- fetched_at은 "응답을 모두 받은 직후"(API 모드) 또는 사용자가 명시한 시각(파일 모드)이며, 상태가
  관측된 시각이 아니다.
- API 키와 요청 URL은 stdout, stderr, 예외 메시지, JSONL 어디에도 쓰지 않는다.
- Alembic과 같은 프로세스에서 쓰지 않는다(alembic은 기존 로거를 끈다). 이 명령은 로깅 설정을 바꾸지 않고
  출력은 print로 한다.
"""

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.clients.oa15994 import SERVICE_NAME, Oa15994ParseError, load_sample_rows, parse_elevators
from app.clients.seoul_open_api import (
    CODE_NO_DATA,
    SeoulOpenApiClient,
    SeoulOpenApiConfigError,
    SeoulOpenApiError,
    SeoulOpenApiResponseError,
    parse_envelope,
    redact,
    strip_urls,
)
from app.config import settings
from app.database import make_engine
from app.models import StationExternalId
from app.services.elevator_ingest import (
    SOURCE,
    ElevatorIngestError,
    InvalidFetchedAtError,
    validate_fetched_at,
    ingest_elevators,
)
from app.services.ingestion_log import (
    DEFAULT_LOG_PATH,
    IngestionLogError,
    append_entries,
    check_writable,
    make_entries,
    new_run_id,
)
from app.timeutil import to_utc_iso, utc_now

EXIT_OK = 0
EXIT_DATA_ERROR = 1
EXIT_NOT_READY = 2
EXIT_API_ERROR = 3
EXIT_LOG_FAILED = 4

SCHEMA_VERSION = 1
PROJECT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
REQUIRED_TABLES = ("stations", "station_external_ids", "elevators", "elevator_status")

COUNT_KEYS = (
    "input_rows",
    "ev_elevators",
    "excluded_by_type",
    "elevators_created",
    "elevators_updated",
    "elevators_unchanged",
    "status_created",
    "status_updated",
    "status_unchanged",
    "status_unknown",
    "unmapped",
)


class CliFailure(Exception):
    """종료 코드와 함께 실행을 끝내는 오류. 메시지에는 키와 URL을 넣지 않는다."""

    def __init__(self, exit_code: int, kind: str, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.kind = kind
        self.message = message


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # argparse가 직접 종료하지 않고 우리 종료 코드로 끝나게 한다
        raise CliFailure(EXIT_NOT_READY, "usage", message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="collect_elevators",
        description="OA-15994 엘리베이터를 수집해 적재한다. 기본은 dry-run이다.",
    )
    parser.add_argument("--apply", action="store_true", help="실제로 DB에 반영한다. 없으면 dry-run이다.")
    parser.add_argument("--database-url", default=None, help="기본값은 설정(DATABASE_URL)의 DB다.")
    parser.add_argument("--input-file", type=Path, default=None, help="파일 모드: 저장된 응답 JSON 파일.")
    parser.add_argument("--base-url", default=None, help="API 모드: 확인된 HTTPS 주소(https://...).")
    parser.add_argument("--fetched-at", default=None, help="파일 모드 필수: UTC 시각 YYYY-MM-DDTHH:MM:SSZ.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="결과를 JSON 한 개로 출력한다.")
    return parser


# ---------- 결과 ----------


@dataclass
class Outcome:
    run_id: str
    exit_code: int = EXIT_OK
    mode: str | None = None
    dry_run: bool = True
    committed: bool = False
    fetched_at: str | None = None
    counts: dict[str, Any] = field(default_factory=lambda: {key: None for key in COUNT_KEYS})
    unmapped_station_codes: list[str] = field(default_factory=list)
    log: dict[str, Any] = field(default_factory=lambda: {"path": None, "enabled": False, "entries": 0, "written": None})
    warnings: list[str] = field(default_factory=list)
    error: dict[str, str] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": self.exit_code == EXIT_OK,
            "partial_success": self.exit_code == EXIT_LOG_FAILED,  # DB는 커밋되었고 로그 기록만 실패
            "exit_code": self.exit_code,
            "run_id": self.run_id,
            "mode": self.mode,
            "dry_run": self.dry_run,
            "committed": self.committed,
            "fetched_at": self.fetched_at,
            "counts": self.counts,
            "unmapped_station_codes": self.unmapped_station_codes,
            "log": self.log,
            "warnings": self.warnings,
            "error": self.error,
        }


# ---------- 경로·DB 검사 ----------


def _sqlite_path(url: str) -> Path | None:
    parsed = make_url(url)
    database = parsed.database
    if not parsed.drivername.startswith("sqlite") or not database or database == ":memory:" or database.startswith("file:"):
        return None
    return Path(database).expanduser().resolve()  # 상대 경로와 심볼릭 링크를 풀어 실제 경로로 만든다


def _temp_roots() -> list[Path]:
    roots = []
    for candidate in (tempfile.gettempdir(), "/tmp", "/var/tmp"):
        path = Path(candidate)
        if path.exists():
            roots.append(path.resolve())
    return roots


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a == b or (a.exists() and b.exists() and os.path.samefile(a, b))
    except OSError:
        return False


def check_file_mode_target(database_url: str, default_database_url: str) -> None:
    """파일 모드가 접근할 수 있는 DB인지 검사한다. --apply 여부와 관계없이(dry-run 포함) 적용한다.

    DB를 열지 않고 경로만 본다. 호출하는 쪽은 이 검사를 DB 엔진을 만들기 전에 해야 한다.
    거부: 기본 DB, 저장소 data/ 아래 DB, 임시 디렉터리 밖의 DB, SQLite 파일이 아닌 DB.
    같은 파일을 가리키는 다른 표기(상대 경로, 심볼릭 링크, 하드 링크, 대소문자만 다른 경로)도 거부한다.
    """
    target = _sqlite_path(database_url)
    if target is None:
        raise CliFailure(EXIT_NOT_READY, "not_ready", "파일 모드는 임시 SQLite 파일 DB에서만 허용됩니다(dry-run 포함).")
    default = _sqlite_path(default_database_url)
    if default is not None and _same_file(target, default):
        raise CliFailure(EXIT_NOT_READY, "not_ready", "파일 모드는 기본 DB에 접근할 수 없습니다(dry-run 포함, 실제 경로 기준).")
    data_dir = PROJECT_DATA_DIR.resolve()
    if target == data_dir or data_dir in target.parents:
        raise CliFailure(EXIT_NOT_READY, "not_ready", "파일 모드는 저장소 data/ 아래 DB에 접근할 수 없습니다(dry-run 포함, 실제 경로 기준).")
    if not any(root in target.parents for root in _temp_roots()):
        raise CliFailure(EXIT_NOT_READY, "not_ready", "파일 모드는 임시 디렉터리의 테스트 DB에서만 허용됩니다(dry-run 포함).")


def _check_database_ready(engine, database_url: str) -> None:
    path = _sqlite_path(database_url)
    if make_url(database_url).drivername.startswith("sqlite") and path is not None and not path.exists():
        raise CliFailure(EXIT_NOT_READY, "not_ready", "SQLite 파일이 없습니다. alembic upgrade head를 먼저 실행하세요.")
    try:
        inspector = inspect(engine)
        missing = [t for t in REQUIRED_TABLES if not inspector.has_table(t)]
        columns = {c["name"] for c in inspector.get_columns("station_external_ids")} if not missing else set()
    except OperationalError:
        raise CliFailure(EXIT_NOT_READY, "not_ready", "DB에 연결하거나 읽을 수 없습니다.") from None
    if missing or "station_line_id" not in columns:
        raise CliFailure(EXIT_NOT_READY, "not_ready", "DB가 최신 마이그레이션 상태가 아닙니다. alembic upgrade head를 먼저 실행하세요.")


# ---------- 입력 ----------


def _validate_https_base_url(base_url: str | None) -> str:
    if not base_url:
        raise CliFailure(
            EXIT_NOT_READY, "not_ready",
            "API 모드는 확인된 HTTPS 주소(--base-url https://...)가 있어야 실행됩니다. HTTPS 연결이 확인되지 않았다면 사용할 수 없습니다.",
        )
    parts = urlsplit(base_url)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
        raise CliFailure(
            EXIT_NOT_READY, "not_ready",
            "--base-url은 사용자 정보와 쿼리가 없는 https:// 주소여야 합니다. 평문 HTTP는 허용되지 않습니다.",
        )
    return base_url


def _read_input_file(path: Path) -> list[dict]:
    """저장된 파일에서 row 배열을 꺼낸다. 원본 응답(RESULT 포함)과 정리된 샘플 구조를 모두 받는다."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise CliFailure(EXIT_NOT_READY, "not_ready", "입력 파일을 읽을 수 없습니다.") from None
    try:
        payload = json.loads(text)
    except ValueError:
        raise CliFailure(EXIT_DATA_ERROR, "data", "입력 파일이 JSON이 아닙니다.") from None
    if not isinstance(payload, dict):
        raise CliFailure(EXIT_DATA_ERROR, "data", "입력 파일의 최상위가 객체가 아닙니다.")
    try:
        if SERVICE_NAME not in payload and "RESULT" not in payload and "service" in payload and "row" in payload:
            return load_sample_rows(payload)  # 정리된 샘플 구조
        if SERVICE_NAME in payload or "RESULT" in payload:
            page = parse_envelope(payload, SERVICE_NAME)  # 원본 응답 한 페이지
            if page.code == CODE_NO_DATA:
                return []
            if page.total != len(page.rows):
                raise CliFailure(
                    EXIT_DATA_ERROR, "data",
                    f"입력 파일은 일부 페이지뿐입니다(전체 {page.total}건 중 {len(page.rows)}건). 전체 응답이 필요합니다.",
                )
            return page.rows
    except SeoulOpenApiResponseError as exc:
        raise CliFailure(EXIT_DATA_ERROR, "data", str(exc)) from None
    except Oa15994ParseError as exc:
        raise CliFailure(EXIT_DATA_ERROR, "data", str(exc)) from None
    raise CliFailure(EXIT_DATA_ERROR, "data", "입력 파일의 형식을 알 수 없습니다.")


def _mapped_station_codes(engine, codes: set[str]) -> set[str]:
    """OA-15994 역 코드 중 station_external_ids에 매핑이 있는 것. 읽기만 하며 커밋하지 않는다."""
    statement = select(StationExternalId.external_station_code).where(
        StationExternalId.source == SOURCE,
        StationExternalId.external_station_code.in_(codes),
    )
    with engine.connect() as connection:
        return {code for (code,) in connection.execute(statement)}


def _fail_all_unmapped(
    outcome: Outcome,
    records: list,
    unmapped_keys: set[tuple[str, str]],
    *,
    apply: bool,
    log_path: Path,
    clock: Callable[[], datetime],
    api_key: str,
) -> None:
    """모든 설비의 역 코드가 매핑되지 않았을 때의 실패 처리. 항상 CliFailure(1)로 끝난다.

    DB에는 아무것도 쓰지 않는다. --apply이면 진단용 NO_STATION_MATCH 로그만 남기고, dry-run은 파일을 바꾸지 않는다.
    """
    outcome.committed = False
    if apply:
        entries = make_entries(
            run_id=outcome.run_id,
            logged_at=to_utc_iso(clock()),
            source=SOURCE,
            records=records,
            unmapped_keys=unmapped_keys,
            secrets=(api_key,),
        )
        outcome.log["entries"] = len(entries)
        try:
            append_entries(log_path, entries)
            outcome.log["written"] = True
        except IngestionLogError:
            outcome.log["written"] = False
            outcome.warnings.append("미매핑 로그도 기록하지 못했습니다.")
    raise CliFailure(
        EXIT_DATA_ERROR, "data",
        "모든 설비의 역 코드가 매핑되지 않았습니다. DB에 아무것도 저장하지 않았습니다. 역 코드 매핑을 확인하세요.",
    )


def _default_client_factory(api_key: str, base_url: str) -> SeoulOpenApiClient:
    # allow_insecure_http는 쓰지 않는다. 기본값(False)이라 http 주소는 클라이언트도 거부한다.
    return SeoulOpenApiClient(api_key, base_url=base_url)


def _fetch_rows(client_factory: Callable, api_key: str, base_url: str) -> list[dict]:
    client = None
    try:
        client = client_factory(api_key, base_url)
        return client.fetch_all_rows(SERVICE_NAME).rows
    except SeoulOpenApiConfigError as exc:
        raise CliFailure(EXIT_NOT_READY, "not_ready", str(exc)) from None
    except SeoulOpenApiError as exc:
        raise CliFailure(EXIT_API_ERROR, "api", str(exc)) from None
    finally:
        if client is not None:
            client.close()


# ---------- 실행 ----------


def _run(
    args: argparse.Namespace,
    outcome: Outcome,
    *,
    client_factory: Callable,
    clock: Callable[[], datetime],
    log_path: Path,
    api_key: str,
    default_database_url: str,
) -> None:
    apply = bool(args.apply)
    outcome.dry_run = not apply
    file_mode = args.input_file is not None
    outcome.mode = "file" if file_mode else "api"

    # 1) 인자 검증 (DB를 열기 전)
    if file_mode:
        if args.base_url:
            raise CliFailure(EXIT_NOT_READY, "usage", "파일 모드에서는 --base-url을 쓸 수 없습니다.")
        if not args.fetched_at:
            raise CliFailure(EXIT_NOT_READY, "usage", "파일 모드는 --fetched-at(UTC)을 반드시 명시해야 합니다.")
        try:
            fetched_at = validate_fetched_at(args.fetched_at)
        except InvalidFetchedAtError as exc:
            raise CliFailure(EXIT_NOT_READY, "usage", str(exc)) from None
    else:
        if args.fetched_at:
            raise CliFailure(EXIT_NOT_READY, "usage", "API 모드에서는 --fetched-at을 쓸 수 없습니다(수집 시각은 자동으로 기록됩니다).")
        base_url = _validate_https_base_url(args.base_url)
        if not api_key.strip():
            raise CliFailure(EXIT_NOT_READY, "not_ready", "API 키가 설정되지 않았습니다.")
        fetched_at = None

    database_url = args.database_url or default_database_url

    # 2) 파일 모드의 DB 위치 검사. --apply 여부와 관계없이 하며, DB 엔진을 만들기 전에 한다.
    if file_mode:
        check_file_mode_target(database_url, default_database_url)

    # 3) 로그를 쓸 수 있는지 확인 (DB 커밋 전). dry-run은 파일을 만들지 않는다.
    if apply:
        outcome.log.update({"path": str(log_path), "enabled": True})
        try:
            check_writable(log_path)
        except IngestionLogError as exc:
            raise CliFailure(EXIT_NOT_READY, "not_ready", str(exc)) from None

    engine = make_engine(database_url)
    try:
        # 4) DB 준비 상태 확인 (읽기만)
        _check_database_ready(engine, database_url)

        # 5) 입력 수집: 외부 호출은 DB 쓰기 트랜잭션을 열기 전에 끝낸다.
        if file_mode:
            rows = _read_input_file(args.input_file)
        else:
            rows = _fetch_rows(client_factory, api_key, base_url)
            fetched_at = to_utc_iso(clock())  # 응답을 모두 받은 직후의 UTC 시각
        outcome.fetched_at = fetched_at

        # 6) 파싱: EV만 추출. 0건이면 오류.
        try:
            parsed = parse_elevators(rows)
        except Oa15994ParseError as exc:
            raise CliFailure(EXIT_DATA_ERROR, "data", str(exc)) from None
        outcome.counts["input_rows"] = parsed.total_rows
        outcome.counts["ev_elevators"] = len(parsed.elevators)
        outcome.counts["excluded_by_type"] = dict(sorted(parsed.excluded_by_type.items()))
        if not parsed.elevators:
            raise CliFailure(EXIT_DATA_ERROR, "data", "EV 설비가 0건입니다. 기존 데이터를 바꾸지 않습니다.")

        # 6-1) 전부 미매핑이면 적재 함수를 부르지 않고 실패한다(커밋 자체가 일어나지 않는다).
        #      읽기 전용 SELECT 한 번으로 확인한다. Upsert 서비스의 구조는 바꾸지 않는다.
        station_codes = {r.station_code for r in parsed.elevators}
        if not _mapped_station_codes(engine, station_codes):
            for key in COUNT_KEYS[3:]:
                outcome.counts[key] = 0
            outcome.counts["unmapped"] = len(parsed.elevators)
            outcome.unmapped_station_codes = sorted(station_codes)
            _fail_all_unmapped(
                outcome, parsed.elevators, {r.key for r in parsed.elevators},
                apply=apply, log_path=log_path, clock=clock, api_key=api_key,
            )

        # 7) 적재: 이 명령 전용의 새 Session 하나. dry-run이면 계산 후 롤백한다.
        try:
            with Session(engine) as session:
                report = ingest_elevators(session, parsed.elevators, fetched_at=fetched_at, dry_run=not apply)
        except ElevatorIngestError as exc:
            raise CliFailure(EXIT_DATA_ERROR, "data", str(exc)) from None
        except OperationalError as exc:
            if "locked" in str(exc).lower():
                raise CliFailure(
                    EXIT_DATA_ERROR, "data",
                    "DB가 잠겨 있어 적재하지 못했습니다(전체 롤백). 다른 프로세스의 쓰기가 끝난 뒤 다시 실행하세요.",
                ) from None
            raise CliFailure(EXIT_DATA_ERROR, "data", "DB 오류로 적재하지 못했습니다(전체 롤백).") from None
    finally:
        engine.dispose()

    counts = report.as_counts()
    for key in COUNT_KEYS[3:]:
        outcome.counts[key] = counts[key]
    outcome.unmapped_station_codes = report.unmapped_station_codes
    all_unmapped = report.unmapped_count == len(parsed.elevators)
    # 커밋된 것은 --apply이고 전부 미매핑이 아닐 때뿐이다(전부 미매핑이면 아무것도 저장하지 않는다).
    outcome.committed = apply and not all_unmapped

    if report.unmapped_count and not all_unmapped:
        outcome.warnings.append(
            f"역 코드 매핑이 없어 건너뛴 설비 {report.unmapped_count}건(역 코드 {len(report.unmapped_station_codes)}개). 나머지는 정상 처리했습니다."
        )
    if report.status_unknown:
        outcome.warnings.append(
            f"상태를 해석하지 못한 설비 {report.status_unknown}건. 설비 정보만 저장하고 상태는 만들거나 바꾸지 않았습니다."
        )

    if all_unmapped:
        # 안전망: 사전 확인을 통과했지만 적재 시점에 전부 미매핑으로 나온 경우(그 사이 매핑이 바뀐 경우 등).
        # 적재 함수는 아무것도 저장하지 않았다.
        _fail_all_unmapped(
            outcome, parsed.elevators, {(u.station_code, u.elevator_name) for u in report.unmapped},
            apply=apply, log_path=log_path, clock=clock, api_key=api_key,
        )

    # 8) 로그 기록 (--apply). 미매핑과 미확인 상태를 JSONL에 추가한다.
    entries: list[dict[str, str]] = []
    if apply:
        unmapped_keys = {(u.station_code, u.elevator_name) for u in report.unmapped}
        entries = make_entries(
            run_id=outcome.run_id,
            logged_at=to_utc_iso(clock()),
            source=SOURCE,
            records=parsed.elevators,
            unmapped_keys=unmapped_keys,
            secrets=(api_key,),
        )
        outcome.log["entries"] = len(entries)

    if apply:
        try:
            append_entries(log_path, entries)
            outcome.log["written"] = True
        except IngestionLogError as exc:
            outcome.log["written"] = False
            # DB는 이미 커밋되었다. 롤백되지 않았음을 분명히 밝힌다.
            raise CliFailure(
                EXIT_LOG_FAILED, "log",
                f"DB 반영은 커밋되어 유지됩니다(롤백되지 않았습니다). 그러나 로그 기록에 실패했습니다: {exc}. "
                "로그 문제를 해결한 뒤 같은 명령을 다시 실행하면 DB는 그대로 두고 로그를 다시 남길 수 있습니다.",
            ) from None


def _render_text(outcome: Outcome) -> tuple[str, str]:
    """(stdout, stderr) 텍스트를 만든다."""
    out: list[str] = []
    err: list[str] = []
    if outcome.mode is not None:
        if outcome.exit_code == EXIT_LOG_FAILED:
            out.append("[부분 성공] DB 반영은 커밋되었고 유지됩니다(롤백되지 않았습니다). 로그 기록은 실패했습니다.")
        elif outcome.dry_run:
            out.append("[dry-run] DB와 로그 파일을 변경하지 않았습니다. 반영하려면 --apply를 명시하세요.")
        elif outcome.committed:
            out.append("DB에 반영했습니다(커밋됨).")
        else:
            out.append("DB에 반영하지 않았습니다.")
        out.append(f"run_id: {outcome.run_id} | 모드: {outcome.mode}")
    if outcome.fetched_at:
        out.append(f"수집 시각(fetched_at): {outcome.fetched_at}  ※ 수집·적재 시각이며 상태 관측 시각이 아닙니다.")
    c = outcome.counts
    if c["ev_elevators"] is not None:
        excluded = ", ".join(f"{k} {v}" for k, v in (c["excluded_by_type"] or {}).items()) or "없음"
        out.append(f"입력: 전체 {c['input_rows']}행, EV 설비 {c['ev_elevators']}건 (제외: {excluded})")
    if c["elevators_created"] is not None:
        out.append(f"설비: 생성 {c['elevators_created']} / 갱신 {c['elevators_updated']} / 변경 없음 {c['elevators_unchanged']}")
        out.append(
            f"상태: 생성 {c['status_created']} / 갱신 {c['status_updated']} / 변경 없음 {c['status_unchanged']} / 미확인 {c['status_unknown']}"
        )
        out.append(f"미매핑: {c['unmapped']}건 (역 코드 {len(outcome.unmapped_station_codes)}개)")
    if outcome.log["enabled"]:
        state = {True: "기록함", False: "기록 실패", None: "기록 전"}[outcome.log["written"]]
        out.append(f"로그: {outcome.log['entries']}건 {state} ({outcome.log['path']})")
    elif outcome.mode is not None and outcome.dry_run:
        out.append("로그: dry-run이라 기록하지 않았습니다.")
    for warning in outcome.warnings:
        err.append(f"경고: {warning}")
    if outcome.error:
        err.append(f"오류[{outcome.error['kind']}]: {outcome.error['message']}")
    return "\n".join(out), "\n".join(err)


def main(
    argv: list[str] | None = None,
    *,
    client_factory: Callable | None = None,
    clock: Callable[[], datetime] | None = None,
    log_path: Path | None = None,
    run_id_factory: Callable[[], str] | None = None,
    api_key: str | None = None,
) -> int:
    """종료 코드를 돌려준다. 인자는 테스트에서 주입할 수 있다(기본값은 실제 설정)."""
    outcome = Outcome(run_id=(run_id_factory or new_run_id)())
    secret = settings.seoul_open_api_key if api_key is None else api_key
    as_json = "--json" in (argv if argv is not None else sys.argv[1:])

    def scrub(text: str) -> str:
        # 키를 가리고, 외부에서 섞여 들어온 요청 URL은 호스트와 경로까지 통째로 지운다.
        return strip_urls(redact(text, secret or None))

    try:
        args = _build_parser().parse_args(argv)
        as_json = args.as_json
        _run(
            args,
            outcome,
            client_factory=client_factory or _default_client_factory,
            clock=clock or utc_now,
            log_path=log_path or DEFAULT_LOG_PATH,
            api_key=secret,
            default_database_url=settings.database_url,
        )
    except CliFailure as failure:
        outcome.exit_code = failure.exit_code
        outcome.error = {"kind": failure.kind, "message": scrub(failure.message)}
    except Exception as exc:  # 예상하지 못한 오류: 메시지에 URL이 있을 수 있어 종류만 보여 준다.
        outcome.exit_code = EXIT_DATA_ERROR
        outcome.error = {"kind": "unexpected", "message": f"예상하지 못한 오류가 발생했습니다({type(exc).__name__})."}

    outcome.warnings = [scrub(w) for w in outcome.warnings]
    if as_json:
        print(scrub(json.dumps(outcome.to_json(), ensure_ascii=False, indent=2)))
    else:
        text_out, text_err = _render_text(outcome)
        if text_out:
            print(scrub(text_out))
        if text_err:
            print(scrub(text_err), file=sys.stderr)
    return outcome.exit_code


if __name__ == "__main__":
    sys.exit(main())
