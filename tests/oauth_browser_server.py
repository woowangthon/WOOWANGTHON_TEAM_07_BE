"""브라우저 통합 검증 전용 서버. 실제 OAuth 제공자만 대체하고 DB·쿠키·API는 그대로 쓴다.

서버 제품 코드에는 이 대체 함수가 없다. 새 테스트 DB만 허용한다.
"""
import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--port", type=int, default=18040)
    parser.add_argument("--frontend", default="http://127.0.0.1:5179")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = Path(args.database).resolve()
    allowed = (root.parent / ".tmp").resolve()
    if allowed not in target.parents or target.exists():
        raise SystemExit("워크스페이스 .tmp 안의 새 테스트 DB 경로만 허용합니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.environ.update(DATABASE_URL=f"sqlite:///{target.as_posix()}",
                      GOOGLE_CLIENT_ID="synthetic-google", GOOGLE_CLIENT_SECRET="synthetic-secret",
                      KAKAO_CLIENT_ID="synthetic-kakao", KAKAO_CLIENT_SECRET="synthetic-secret",
                      AUTH_PUBLIC_BASE_URL=f"http://127.0.0.1:{args.port}",
                      AUTH_FRONTEND_URL=args.frontend + "/#/main", AUTH_COOKIE_SECURE="false",
                      CORS_ALLOWED_ORIGINS=args.frontend)
    sys.path.insert(0, str(root))
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    command.upgrade(cfg, "head")
    from app.database import SessionLocal
    from app.models import Line, Station, StationLine
    with SessionLocal() as db:
        for number in range(1, 9):
            db.add(Line(line_id=str(number), line_name=f"{number}호선"))
        db.add_all([Station(station_id=1, station_name="서울역"), Station(station_id=3, station_name="강남")])
        db.flush()
        db.add_all([StationLine(station_id=1, line_id="1"), StationLine(station_id=1, line_id="4"), StationLine(station_id=3, line_id="2")])
        db.commit()
    from app.routers import auth
    auth.exchange_identity = lambda provider, code, verifier: ("synthetic-" + provider, "테스트 사용자")
    from app.main import create_app
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
