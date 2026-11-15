"""포트·DB 사전 점검 후 단일 서버를 실행한다. DB 변경은 선택형 자동 수집만 수행한다."""

import argparse
import socket
import sys
from pathlib import Path

import uvicorn
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database import SessionLocal
from app.errors import AppError
from app.services.elevator_refresh import validate_refresh_settings
from app.services.readiness import check_readiness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--refresh-elevators", action="store_true", help="시작 직후 및 주기적으로 실제 상태를 수집·적재한다.")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("포트는 1~65535 범위여야 합니다.")
    # 미리 점유한 소켓을 uvicorn에 넘겨 검사~서버 시작 사이의 포트 경쟁도 방지한다.
    server_socket = None
    try:
        address = socket.getaddrinfo(args.host, args.port, type=socket.SOCK_STREAM)[0]
        family, socktype, proto, _, sockaddr = address
        server_socket = socket.socket(family, socktype, proto)
        if sys.platform == "win32":
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        server_socket.bind(sockaddr)
        server_socket.listen(128)
        settings.elevator_auto_refresh = settings.elevator_auto_refresh or args.refresh_elevators
        validate_refresh_settings()
        url = make_url(settings.database_url)
        if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
            if not Path(url.database).is_file():
                raise ValueError("DB 파일이 없습니다. 백업·준비 후 uv run alembic upgrade head를 실행해 주세요.")
        with SessionLocal() as db:
            state = check_readiness(db)
        print(f"DB 준비 완료. 엘리베이터 상태: 최신 {state['elevator_status']['fresh']}건 / 전체 {state['elevator_status']['total']}건", flush=True)
        from app.main import create_app
        server = uvicorn.Server(uvicorn.Config(create_app(), host=args.host, port=args.port, access_log=False))
        server.run(sockets=[server_socket])
        return 0 if server.started else 1
    except OSError:
        print(f"서버 소켓을 열 수 없습니다(포트 {args.port}). PowerShell에서 Get-NetTCPConnection -LocalPort {args.port} -State Listen으로 기존 서버를 확인하세요.", file=sys.stderr)
        return 2
    except (ValueError, AppError) as exc:
        print(exc.message if isinstance(exc, AppError) else str(exc), file=sys.stderr)
        return 2
    except SQLAlchemyError:
        print("DB 점검 실패. DATABASE_URL, 파일 권한, 잠금 상태를 확인해 주세요.", file=sys.stderr)
        return 2
    finally:
        if server_socket is not None:
            server_socket.close()


if __name__ == "__main__":
    sys.exit(main())
