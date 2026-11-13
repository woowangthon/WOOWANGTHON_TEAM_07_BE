"""1~8호선 역·노선 시드 적재 명령.

    uv run python -m app.cli.load_seed --dry-run
    uv run python -m app.cli.load_seed

- 외부 API를 호출하지 않는다. data/seed의 파일만 읽는다.
- --dry-run은 전체를 적용해 본 뒤 롤백한다. DB는 바뀌지 않는다.
- 충돌이 있으면 롤백하고 종료 코드 1로 끝난다.
- DB가 마이그레이션되지 않았거나(station_line_id 컬럼 없음) SQLite 파일이 없으면 종료 코드 2다.
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import settings
from app.database import make_engine
from app.services.station_seed import DEFAULT_SEED_DIR, SeedError, load_seed, read_seed_dir

EXIT_OK = 0
EXIT_SEED_ERROR = 1
EXIT_NOT_READY = 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="1~8호선 역·노선 시드를 멱등하게 적재한다.")
    parser.add_argument("--seed-dir", type=Path, default=DEFAULT_SEED_DIR)
    parser.add_argument(
        "--database-url", default=None, help="기본값은 설정(DATABASE_URL)의 DB다."
    )
    parser.add_argument("--dry-run", action="store_true", help="적용해 본 뒤 롤백한다.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    url = args.database_url or settings.database_url

    parsed = make_url(url)
    if parsed.drivername.startswith("sqlite") and parsed.database not in (None, "", ":memory:"):
        if not Path(parsed.database).exists():
            print(f"오류: SQLite 파일이 없습니다: {parsed.database} (alembic upgrade head를 먼저 실행하세요)", file=sys.stderr)
            return EXIT_NOT_READY

    try:
        rows, links, review = read_seed_dir(args.seed_dir)
    except SeedError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return EXIT_SEED_ERROR

    engine = make_engine(url)
    try:
        inspector = inspect(engine)
        if not inspector.has_table("station_external_ids") or "station_line_id" not in {
            c["name"] for c in inspector.get_columns("station_external_ids")
        }:
            print("오류: DB가 최신 마이그레이션 상태가 아닙니다. alembic upgrade head를 먼저 실행하세요.", file=sys.stderr)
            return EXIT_NOT_READY
        with Session(engine) as session:
            try:
                report = load_seed(session, rows, links, review, dry_run=args.dry_run)
            except SeedError as exc:
                print(f"오류: {exc}\n전체 변경을 롤백했습니다.", file=sys.stderr)
                return EXIT_SEED_ERROR
    finally:
        engine.dispose()

    print("[dry-run] 변경 없음(롤백됨)" if report.dry_run else "적재 완료")
    for key in sorted(report.counts):
        print(f"  {key}: {report.counts[key]}")
    print(f"  review 대기: 환승 후보 {report.review.transfer_pending}건, 역명 차이 {report.review.name_variant_pending}건")
    print(f"  확정된 환승 그룹: {len(report.review.confirmed_groups)}개 (역명·주소가 같다는 이유로 자동 병합하지 않음)")
    print("  참고: 1호선은 일부 구간(data/seed/manifest.json 참고)만 포함합니다.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
