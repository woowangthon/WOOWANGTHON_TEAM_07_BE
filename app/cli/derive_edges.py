"""시간표에서 인접역 간선을 뽑아 station_edges에 적재하는 명령. 외부 API를 호출하지 않는다.

    uv run python -m app.cli.derive_edges            # dry-run: 적용해 본 뒤 롤백
    uv run python -m app.cli.derive_edges --apply    # 실제 반영

- 먼저 collect_timetable --apply로 train_schedules를 채워야 한다.
- 환승 간선은 load_edges(공식 OA-13290 근거)가 맡는다. 이 명령은 ADJACENT만 만든다.
- 기존 간선은 지우거나 덮어쓰지 않는다. 같은 입력을 다시 넣어도 늘지 않는다.

종료 코드: 0 정상, 1 적재 오류(롤백), 2 실행 준비 오류(DB 미준비, 시간표 없음)
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy import func, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import settings
from app.database import make_engine
from app.models import TrainSchedule
from app.services.edge_derive import MIN_TRAINS, load_derived_edges

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_READY = 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="시간표에서 인접역 간선을 뽑아 적재한다. 기본은 dry-run이다.")
    parser.add_argument("--database-url", default=None, help="기본값은 설정(DATABASE_URL)의 DB다.")
    parser.add_argument("--min-trains", type=int, default=MIN_TRAINS, help="간선으로 인정할 최소 열차 수.")
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

    engine = make_engine(url)
    try:
        tables = inspect(engine)
        if not tables.has_table("station_edges") or not tables.has_table("train_schedules"):
            print("오류: 테이블이 없습니다. alembic upgrade head를 먼저 실행하세요.", file=sys.stderr)
            return EXIT_NOT_READY
        with Session(engine) as session:
            if session.scalar(select(func.count()).select_from(TrainSchedule)) == 0:
                print("오류: train_schedules가 비어 있습니다. collect_timetable --apply를 먼저 실행하세요.", file=sys.stderr)
                return EXIT_NOT_READY
            try:
                report = load_derived_edges(session, dry_run=not args.apply, min_trains=args.min_trains)
            except Exception as exc:  # 롤백은 서비스가 했다
                print(f"오류: 간선 적재에 실패했습니다({type(exc).__name__}). 전체 변경을 롤백했습니다.", file=sys.stderr)
                return EXIT_ERROR
    finally:
        engine.dispose()

    print("[dry-run] 변경 없음(롤백됨)" if report.dry_run else "적재 완료")
    for key in sorted(report.counts):
        print(f"  {key}: {report.counts[key]}")
    print("  참고: 시간표 기반 추출이며 공식 인접역 자료가 아닙니다. 급행은 제외했습니다.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
