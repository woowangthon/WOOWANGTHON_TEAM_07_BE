"""OA-101 열차 시간표 수집 명령. 이슈 #34.

    uv run python -m app.cli.collect_timetable                 # dry-run: 받아서 적용해 본 뒤 롤백
    uv run python -m app.cli.collect_timetable --apply         # 실제 반영
    uv run python -m app.cli.collect_timetable --station-code 0150 --station-code 0222   # 일부 역만

- 역 코드는 DB의 OA-12035 외부 코드(노선 연결이 있는 것) 전체가 기본이다. 역 x 요일 3 x 방향 2번 호출한다.
- 수집을 모두 끝낸 뒤에만 DB에 쓴다. 하나라도 실패하면 기존 데이터를 그대로 둔다.
- API 키와 요청 URL은 출력하지 않는다. 주소를 생략하면 문서화된 기본 주소(HTTP)를 쓴다.

종료 코드
    0 정상
    1 데이터·적재 오류 (응답 형식·필드 타입, 노선 미확인·불일치, 중복 열차 번호, 저장된 것보다 오래된 수집 등).
      전체를 롤백했고 기존 행과 fetched_at은 그대로다(#71).
    2 실행 준비 오류 (키 없음, DB 미준비, 인자 오류)
    3 외부 API 오류. 기존 DB는 그대로다.
"""

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.clients.seoul_open_api import (
    DEFAULT_BASE_URL,
    SeoulOpenApiClient,
    SeoulOpenApiConfigError,
    SeoulOpenApiError,
    redact,
    strip_urls,
)
from app.config import settings
from app.database import make_engine
from app.services.timetable import SERVICE_DAY_TYPES, TimetableError
from app.services.timetable_ingest import fetch_timetables, load_timetables, station_codes_in_db
from app.timeutil import to_utc_iso, utc_now

EXIT_OK = 0
EXIT_DATA_ERROR = 1
EXIT_NOT_READY = 2
EXIT_API_ERROR = 3


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OA-101 열차 시간표를 수집해 적재한다. 기본은 dry-run이다.")
    parser.add_argument("--database-url", default=None, help="기본값은 설정(DATABASE_URL)의 DB다.")
    parser.add_argument("--base-url", default=None, help="서울 열린데이터광장 주소(http:// 또는 https://). 생략하면 기본 주소.")
    parser.add_argument("--station-code", action="append", default=None, help="받을 역 코드(여러 번 가능). 생략하면 전체.")
    parser.add_argument(
        "--day-type", action="append", choices=SERVICE_DAY_TYPES, default=None, help="받을 요일 종류(여러 번 가능). 생략하면 전체."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="적용해 본 뒤 롤백한다(기본).")
    mode.add_argument("--apply", action="store_true", help="실제로 반영한다.")
    return parser.parse_args(argv)


def _valid_base_url(base_url: str | None) -> str | None:
    if not base_url:
        return DEFAULT_BASE_URL
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
        return None
    return base_url


def _default_client_factory(api_key: str, base_url: str) -> SeoulOpenApiClient:
    return SeoulOpenApiClient(api_key, base_url=base_url)


def main(
    argv: list[str] | None = None,
    *,
    client_factory: Callable | None = None,
    api_key: str | None = None,
) -> int:
    args = _parse_args(argv)
    secret = settings.seoul_open_api_key if api_key is None else api_key

    def say(text: str, *, err: bool = False) -> None:
        print(strip_urls(redact(text, secret or None)), file=sys.stderr if err else sys.stdout)

    base_url = _valid_base_url(args.base_url)
    if base_url is None:
        say("오류: --base-url은 사용자 정보와 쿼리가 없는 http:// 또는 https:// 주소여야 합니다.", err=True)
        return EXIT_NOT_READY
    if not secret.strip():
        say("오류: API 키가 설정되지 않았습니다(.env의 SEOUL_OPEN_API_KEY).", err=True)
        return EXIT_NOT_READY

    url = args.database_url or settings.database_url
    parsed = make_url(url)
    if parsed.drivername.startswith("sqlite") and parsed.database not in (None, "", ":memory:"):
        if not Path(parsed.database).exists():
            say(f"오류: SQLite 파일이 없습니다: {parsed.database}", err=True)
            return EXIT_NOT_READY

    engine = make_engine(url)
    try:
        if not inspect(engine).has_table("train_schedules"):
            say("오류: train_schedules 테이블이 없습니다. alembic upgrade head를 먼저 실행하세요.", err=True)
            return EXIT_NOT_READY
        with Session(engine) as session:
            codes = sorted(set(args.station_code)) if args.station_code else station_codes_in_db(session)
            if not codes:
                say("오류: 받을 역 코드가 없습니다. 역 시드(load_seed)를 먼저 적재하세요.", err=True)
                return EXIT_NOT_READY
            day_types = tuple(args.day_type) if args.day_type else SERVICE_DAY_TYPES
            say(f"수집 시작: 역 {len(codes)}개 x 요일 {len(day_types)} x 방향 2")

            client = None
            try:
                client = (client_factory or _default_client_factory)(secret, base_url)
                collected = fetch_timetables(client, codes, day_types=day_types)
            except SeoulOpenApiConfigError as exc:
                say(f"오류: {exc}", err=True)
                return EXIT_NOT_READY
            except SeoulOpenApiError as exc:
                say(f"외부 API 오류: {exc}\n기존 데이터는 그대로입니다.", err=True)
                return EXIT_API_ERROR
            except TimetableError as exc:
                say(f"응답 형식 오류: {exc}\n기존 데이터는 그대로입니다.", err=True)
                return EXIT_DATA_ERROR
            finally:
                if client is not None:
                    client.close()

            try:
                report = load_timetables(
                    session, collected, fetched_at=to_utc_iso(utc_now()), dry_run=not args.apply
                )
            except TimetableError as exc:
                say(f"적재 오류: {exc}\n전체 변경을 롤백했습니다.", err=True)
                return EXIT_DATA_ERROR
    finally:
        engine.dispose()

    say("[dry-run] 변경 없음(롤백됨)" if report.dry_run else "적재 완료")
    for key in sorted(report.counts):
        say(f"  {key}: {report.counts[key]}")
    say("  참고: 계획 시각이며 지연은 반영되지 않습니다. WEEK_TAG와 INOUT_TAG의 의미는 추정입니다.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
