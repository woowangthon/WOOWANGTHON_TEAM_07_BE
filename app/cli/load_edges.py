"""공식 근거로 검증된 역 간선(VERIFIED, 현재 TRANSFER) 적재 명령.

    uv run python -m app.cli.load_edges            # 기본은 dry-run: 적용해 본 뒤 롤백한다
    uv run python -m app.cli.load_edges --apply    # 실제 반영(명시했을 때만)

- data/seed/station_edges_verified.csv만 읽는다. 외부 API를 호출하지 않는다.
- 공식 근거가 없는 ADJACENT(PROVISIONAL)는 읽지도 적재하지도 않는다. 이를 켜는 옵션은 없다.
- 역 시드(load_seed)가 먼저 적재되어 있어야 한다.
- 매핑 실패나 충돌이 있으면 전체를 롤백하고 종료 코드 1로 끝난다.
- DB가 마이그레이션되지 않았거나 SQLite 파일이 없으면 종료 코드 2다.
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import settings
from app.database import make_engine
from app.services.edge_seed import load_edges, read_verified_edges
from app.services.station_seed import DEFAULT_SEED_DIR, SeedError

EXIT_OK = 0
EXIT_SEED_ERROR = 1
EXIT_NOT_READY = 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="검증된 역 간선을 멱등하게 적재한다.")
    parser.add_argument("--seed-dir", type=Path, default=DEFAULT_SEED_DIR)
    parser.add_argument("--database-url", default=None, help="기본값은 설정(DATABASE_URL)의 DB다.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="적용해 본 뒤 롤백한다(기본).")
    mode.add_argument("--apply", action="store_true", help="실제로 반영한다.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    url = args.database_url or settings.database_url

    parsed = make_url(url)
    if parsed.drivername.startswith("sqlite") and parsed.database not in (None, "", ":memory:"):
        if not Path(parsed.database).exists():
            print(f"오류: SQLite 파일이 없습니다: {parsed.database}", file=sys.stderr)
            return EXIT_NOT_READY

    try:
        edges = read_verified_edges(args.seed_dir)
    except SeedError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return EXIT_SEED_ERROR

    engine = make_engine(url)
    try:
        if not inspect(engine).has_table("station_edges"):
            print("오류: station_edges 테이블이 없습니다. alembic upgrade head를 먼저 실행하세요.", file=sys.stderr)
            return EXIT_NOT_READY
        with Session(engine) as session:
            try:
                report = load_edges(session, edges, dry_run=not args.apply)
            except SeedError as exc:
                print(f"오류: {exc}\n전체 변경을 롤백했습니다.", file=sys.stderr)
                return EXIT_SEED_ERROR
    finally:
        engine.dispose()

    print("[dry-run] 변경 없음(롤백됨)" if report.dry_run else "적재 완료")
    for key in sorted(report.counts):
        print(f"  {key}: {report.counts[key]}")
    print("  참고: 공식 근거가 없는 인접 간선(PROVISIONAL)은 적재하지 않았습니다. 간선 수는 실제 거리나 무장애 이동 가능성이 아닙니다.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
