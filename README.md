# WOOWANGTHON_TEAM_07_BE

지하철 엘리베이터 접근성 안내 및 출발 시각 추천 서비스 백엔드. Python 3.13, FastAPI, SQLite(SQLAlchemy + Alembic).

## 실행 방법

사전 준비: [uv](https://docs.astral.sh/uv/) 설치.

```bash
# 1. 의존성 설치 (.venv 생성)
uv sync

# 2. 환경 변수 파일 준비 (.env는 커밋하지 않는다)
cp .env.example .env
# 필요하면 .env의 SEOUL_OPEN_API_KEY 값을 채운다. 비워 두어도 서버는 뜬다.

# 3. DB 마이그레이션 적용 (data/dev.db 생성)
uv run alembic upgrade head

# 4. 서버 실행
uv run uvicorn app.main:app --reload
```

서버가 뜨면 `GET http://127.0.0.1:8000/health`가 `{"status": "ok"}`를 응답한다.

## 테스트

```bash
uv run pytest
```

테스트는 임시 SQLite 파일에 마이그레이션을 적용해서 실행하므로 `data/dev.db`를 건드리지 않는다.

## 오류 응답 형식

모든 오류 응답은 아래 형식을 따른다.

```json
{"error": {"code": "NOT_FOUND", "message": "요청한 리소스를 찾을 수 없습니다."}}
```

- 서비스 코드에서는 `app.errors.AppError(status_code, code, message)`를 던진다.
- 경로 없음(404)은 `NOT_FOUND`, 허용되지 않은 메서드(405)는 `METHOD_NOT_ALLOWED`, 요청 값 오류(422)는 `VALIDATION_ERROR`, 처리되지 않은 예외(500)는 `INTERNAL_ERROR`다.
