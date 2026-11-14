"""해커톤 시연용 데모 SQLite DB를 임시 디렉터리에 만든다. 실제 data/dev.db는 쓰지 않는다.

    uv run python -m app.cli.build_demo_db --include-provisional-adjacent
    # 만든 DB로 서버 실행(실제 DB는 그대로)
    DATABASE_URL=sqlite:////tmp/.../demo.db uv run uvicorn app.main:app

- 출력 위치는 임시 디렉터리만 허용한다(저장소 안, 비어 있지 않은 디렉터리는 거부).
- --include-provisional-adjacent: 공식 인접 증명이 없는 PROVISIONAL ADJACENT를 **데모 DB에만** 적재한다.
  운영용 `app.cli.load_edges`에는 이 옵션이 없고 안전 제한은 그대로다.
- --no-synthetic: DEMO/SYNTHETIC 합성 상태를 넣지 않는다(실제 샘플만).
- OA-15994 샘플은 과거 응답이며 현재 상태를 보장하지 않는다. 원본에 없는 관측·수집 시각을 만들지 않는다.
- 종료 코드 0 정상, 1 빌드 오류.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from app.services.demo_db import DEFAULT_SAMPLE, ROOT, DemoBuildError, build_demo_db


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="시연용 데모 DB 생성(임시 디렉터리).")
    p.add_argument("--out-dir", type=Path, default=None, help="기본값: 새 임시 디렉터리")
    p.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE)
    p.add_argument("--sample-fetched-at", default=None, help="기본값: 샘플 파일명 날짜의 00:00:00Z(하한값)")
    p.add_argument("--include-provisional-adjacent", action="store_true", help="데모 DB 전용 테스트 옵션")
    p.add_argument("--no-synthetic", action="store_true")
    p.add_argument("--source-db", type=Path, default=ROOT / "data" / "dev.db", help="읽기 전용으로 비교만 한다")
    args = p.parse_args(argv)

    out_dir = args.out_dir or Path(tempfile.mkdtemp(prefix="elevator-demo-"))
    try:
        result = build_demo_db(
            out_dir,
            sample=args.sample,
            sample_fetched_at=args.sample_fetched_at,
            include_provisional_adjacent=args.include_provisional_adjacent,
            synthetic=not args.no_synthetic,
            source_db=args.source_db,
        )
    except DemoBuildError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    print(f"데모 DB: {result.db_path}")
    print(json.dumps(result.manifest["counts"], ensure_ascii=False, indent=2))
    print("주의: 시연용 DB이며 운영 데이터가 아니다. 샘플 엘리베이터는 과거 응답이라 TTL 기준 STALE로 보인다.")
    print(f"자세한 내용: {result.out_dir / 'demo_manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
