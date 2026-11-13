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

## 역·노선 시드 적재

1~8호선 역·노선 시드(OA-12035 기반)는 `data/seed/`에 있다. 출처, 커버리지, REVIEW 규칙은 [data/seed/SOURCES.md](data/seed/SOURCES.md)를 본다. **1호선은 일부 구간만 포함하며, 1~8호선 전체를 확보한 것이 아니다.**

```bash
uv run alembic upgrade head
uv run python -m app.cli.load_seed --dry-run   # 적용해 본 뒤 롤백. DB는 바뀌지 않는다
uv run python -m app.cli.load_seed             # 멱등 적재. 충돌하면 전체 롤백
```

## 엘리베이터 수집 CLI (OA-15994)

서울 열린데이터광장 OA-15994 응답에서 엘리베이터(EV)만 골라 `elevators`, `elevator_status`에 Upsert한다.

> **현재 실제 API 호출은 사용할 수 없다.** 문서화된 주소(`http://openapi.seoul.go.kr:8088/...`)는 평문 HTTP이고 API 키가 URL에 들어간다. 개발 환경에서 HTTPS 연결은 확인되지 않았다(연결 실패 기록이며 지원 여부를 단정하지 않는다). 이 명령은 **HTTPS 주소를 `--base-url`로 명시했을 때만** API를 호출하며, 평문 HTTP를 허용하는 옵션은 없다. 확인되기 전에는 아래 파일 모드와 테스트만 쓴다.
>
> 아래 예시의 `https://<확인된 HTTPS 주소>`는 **자리표시자이며 실제 주소가 아니다.** 사용할 수 있다고 확인된 HTTPS 주소는 아직 없고, 이 문서와 코드는 확인된 HTTPS 주소를 담고 있지 않다(위에 언급한 평문 `http://` 주소는 문서화된 원본 주소일 뿐 이 명령으로는 쓸 수 없다). 그대로 복사해 실행하지 않는다.

### 기본은 dry-run

```bash
# DB와 로그 파일을 바꾸지 않고 무엇이 바뀔지만 보여 준다 (--apply 없음)
uv run python -m app.cli.collect_elevators --base-url https://<확인된 HTTPS 주소>

# 실제 반영은 --apply를 명시했을 때만 한다
uv run python -m app.cli.collect_elevators --base-url https://<확인된 HTTPS 주소> --apply
```

- API 키는 `.env`의 `SEOUL_OPEN_API_KEY`에서만 읽고 출력하지 않는다. 요청 URL도 출력하지 않는다.
- 네트워크 수집은 DB 쓰기 트랜잭션을 열기 전에 끝낸다. 적재는 이 명령 전용의 새 세션 하나로 한다.
- `fetched_at`은 응답을 모두 받은 직후의 UTC 시각이다. **수집 시각이지 상태가 관측된 시각이 아니다.** 원본에 관측 시각이 없어 `observed_at`은 항상 비어 있다.

### 파일 모드 (저장된 응답으로 시험)

```bash
uv run python -m app.cli.collect_elevators \
  --input-file docs/samples/OA-15994_SeoulMetroFaciInfo_2026-11-13.json \
  --fetched-at 2030-01-01T03:00:00Z \
  --database-url sqlite:////tmp/test.db --apply
```

- `--fetched-at`(UTC, `YYYY-MM-DDTHH:MM:SSZ`)이 필수다. 파일에는 수집 시각이 없어서 임의로 채우지 않는다.
- 파일 모드는 `--apply` 여부와 관계없이(**dry-run 포함**) **임시 디렉터리의 SQLite 파일 DB에서만** 동작한다. 기본 DB와 저장소 `data/` 아래 DB는 거부한다. 상대 경로, 심볼릭 링크, 하드 링크, 대소문자만 다른 경로처럼 같은 파일을 가리키는 표기도 모두 거부하며, DB 엔진을 만들기 전에 막는다. 그래서 실제 `data/dev.db`는 dry-run에서도 열리지 않는다.
- 저장소의 샘플 파일을 `data/dev.db`에 적재하는 용도로 쓰지 않는다.

### 결과 처리

| 상황 | 동작 |
|---|---|
| 일부 설비의 역 코드가 매핑 없음 | 경고, 나머지는 정상 적재, `data/logs/ingestion_issues.jsonl`에 `NO_STATION_MATCH` 기록 |
| 모든 설비가 매핑 없음 | 실패(종료 코드 1). 읽기 전용으로 먼저 확인하므로 **커밋이 일어나지 않고** 기존 DB와 `fetched_at`이 그대로다. `--apply`일 때만 진단용 `NO_STATION_MATCH` 로그를 남긴다 |
| 상태를 해석하지 못한 설비 | 설비 행만 저장, 상태 행은 만들지 않음, `UNKNOWN_STATUS`와 원문을 JSONL에 기록 |
| EV가 0건 | 실패(종료 코드 1), 기존 데이터 유지 |
| 응답에 없는 기존 설비 | 삭제하거나 상태를 바꾸지 않음 |
| 외부 API 오류 | 종료 코드 3, 기존 DB와 `fetched_at` 유지 |

- JSONL은 덮어쓰지 않고 한 줄씩 추가하며 `run_id`로 실행을 구분한다. API 키와 요청 URL은 기록하지 않는다. `data/logs/`는 Git에서 제외된다.
- `--apply`는 DB를 바꾸기 전에 로그를 쓸 수 있는지 확인한다. 커밋 뒤 로그 기록이 실패하면 종료 코드 4(부분 성공)다. 이미 커밋된 DB는 롤백되지 않았으며, 같은 명령을 다시 실행하면 DB는 그대로 두고 로그를 남길 수 있다.

### 종료 코드와 `--json`

| 코드 | 의미 |
|---|---|
| 0 | 정상 |
| 1 | 데이터·적재 오류 (파싱 오류, EV 0건, 전부 미매핑, 매핑 충돌, 과거 `fetched_at`, DB 잠금) |
| 2 | 실행 준비 오류 (인자, 키 없음, HTTPS 주소 없음, DB 미준비, 로그 경로 쓰기 불가, 차단된 조합) |
| 3 | 외부 API 오류 |
| 4 | DB 커밋 성공 후 로그 기록 실패 |

`--json`을 주면 결과를 JSON 하나로 출력한다. 키는 `schema_version`, `ok`, `partial_success`, `exit_code`, `run_id`, `mode`, `dry_run`, `committed`, `fetched_at`, `counts`, `unmapped_station_codes`, `log`, `warnings`, `error`이며 실패할 때도 같다.

### 주의

- 이 함수는 세션의 트랜잭션 전체를 커밋하거나 롤백한다. 다른 작업과 같은 세션을 쓰지 않는다(`app/services/elevator_ingest.py` 문서 참고).
- SQLite는 쓰기 잠금이 하나뿐이라 다른 프로세스가 쓰는 중이면 `database is locked`로 실패하고 전체가 롤백된다. 자동 재시도는 하지 않는다.
- Alembic은 같은 프로세스에서 기존 로거를 꺼 버린다. 이 명령은 로깅 설정을 바꾸지 않고 출력은 `print`로 하며, 마이그레이션과 같은 프로세스에서 실행하지 않는다.
- `elevators.station_line_id`는 없다. 설비의 노선은 `elevators.external_station_code`로 `station_external_ids`(source=`OA-15994`)를 조인해 얻는다.
