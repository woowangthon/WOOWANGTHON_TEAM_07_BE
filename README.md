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

## FE/AI 로컬 연동 빠른 시작

운영 DB(`data/dev.db`)를 쓰지 않고 데모 DB로 띄우는 순서다. 연동용 API 설명은 [docs/frontend-integration.md](docs/frontend-integration.md)를 본다.

```bash
# 1. 의존성 설치
uv sync

# 2. 환경 변수 (.env는 커밋하지 않는다). FE 개발 서버 주소를 CORS에 명시한다. 키는 비워도 된다.
cp .env.example .env
#   CORS_ALLOWED_ORIGINS=http://localhost:5173   (쉼표로 여러 개, 와일드카드 불가, 기본은 비어 있어 교차 출처 불허)

# 3. 데모 DB 생성 (임시 디렉터리). 출력의 "데모 DB: <절대경로>/demo.db" 줄을 복사한다
uv run python -m app.cli.build_demo_db --include-provisional-adjacent

# 4. 데모 DB로 서버 실행 (환경 변수가 .env보다 우선한다)
DATABASE_URL=sqlite:///<위에서 출력된 절대경로>/demo.db uv run uvicorn app.main:app --port 8000

# 5. 호출 확인 (한글 쿼리는 --data-urlencode로 보낸다)
curl http://127.0.0.1:8000/health
curl -G http://127.0.0.1:8000/api/stations --data-urlencode "q=서울역"
curl http://127.0.0.1:8000/api/stations/1/elevators
curl http://127.0.0.1:8000/api/stations/1/alternatives
curl "http://127.0.0.1:8000/api/routes?from=1&to=2"
curl "http://127.0.0.1:8000/api/elevators/status?line=2"
curl -X POST http://127.0.0.1:8000/api/departure-time -H "Content-Type: application/json" \
  -d '{"arrival_at":"2026-12-01T09:00:00+09:00","travel_minutes":30}'
curl http://127.0.0.1:8000/api/stations/1/arrivals

# 6. 한 번에 점검 (로컬 서버만 대상. 데이터 없음·미확인은 WARN이며 실패가 아니다)
uv run python scripts/demo_check.py
```

- 자동 문서: 서버 실행 후 `http://127.0.0.1:8000/docs`(Swagger UI).
- 데모 DB의 샘플 엘리베이터는 과거 응답이라 TTL 기준 **정보 없음(STALE)** 으로 나온다. 정상 응답이다. 합성 상태 5건만 생성 시각 기준으로 잠시 확인 상태가 된다.
- 서버 기동과 `/health` 응답은 로컬에서 확인했다. **시연 서버는 집 데스크탑에서 터미널로 실행한다(아래 "시연 서버 실행"). 클라우드 배포는 하지 않았다.**
- 외부 API(OA-15994, OA-101 수집, OA-12764 실시간 도착)는 서버가 호출하지 않는다. 수집은 CLI로만 한다. 도착 정보는 항상 `UNCONFIRMED`다.
- 데모 DB는 인접 간선이 잠정(PROVISIONAL)이다. 시연 서버(아래 "시연 서버 실행")는 시간표에서 추출한 간선을 쓴다.

## 시연 서버 실행 (집 데스크탑, 터미널) (#45)

**결정(2026-11-14): 시연 서버는 집 데스크탑에서 터미널로 직접 실행한다.** Docker, Redis, PostgreSQL은 쓰지 않는다. SQLite 파일(`data/dev.db`)을 그대로 쓰고 호스팅·HTTPS·영속 디스크 문제는 없다. 서버는 상주하는 프로세스 1개다. 실행 기록, 실패 시 동작, 백업, 점검 호출 예는 [docs/demo-runbook.md](docs/demo-runbook.md)에 있다.

### 1회 준비 (순서대로)

```bash
uv sync
cp .env.example .env            # SEOUL_OPEN_API_KEY와 CORS_ALLOWED_ORIGINS를 채운다. .env는 커밋하지 않는다
uv run alembic upgrade head     # data/dev.db 생성·마이그레이션
cp data/dev.db data/dev.db.bak  # 적재 전 백업(서버를 멈춘 상태에서 복사한다)

uv run python -m app.cli.load_seed --dry-run && uv run python -m app.cli.load_seed          # 역·노선 시드
uv run python -m app.cli.load_edges --apply                                                  # 공식 환승 간선(OA-13290)
uv run python -m app.cli.collect_elevators --apply                                           # 엘리베이터 상태(OA-15994) 1회
uv run python -m app.cli.collect_timetable --apply                                           # 열차 시간표(OA-101), 호출 수백 번
uv run python -m app.cli.derive_edges --apply                                                # 시간표에서 인접 간선 추출
```

- 각 명령은 `--apply` 없이 실행하면 dry-run이다. 먼저 dry-run으로 건수를 확인한다.
- 수집 명령은 **외부 API를 호출**하므로 `.env`의 키가 있어야 한다. 주소를 생략하면 문서화된 기본 주소(`http://openapi.seoul.go.kr:8088`)를 쓴다. 평문 HTTP는 팀 결정(2026-11-14)으로 기본 허용이며, 키는 이 PC의 `.env`에만 있고 로그·오류 출력에는 가려진다.
- `collect_timetable`은 역 x 요일 3 x 방향 2번을 호출한다(역 약 280개면 약 1,700번, 호출당 0.2~0.4초). 일부 역만 시험하려면 `--station-code 0150 --station-code 0222 --day-type WEEKDAY`를 쓴다. **일일 호출 한도는 확인하지 못했다.**
- `derive_edges`는 시간표에서 같은 열차의 정차 순서로 인접역을 뽑는다(급행 제외, 2대 이상 확인, 시간 차 20분 이하). 공식 인접역 자료가 아니므로 경로 응답의 `limitations`에 그렇게 표시된다.
- 이 시드·간선·시간표 적재는 **실제 `data/dev.db`를 바꾼다.** 위 백업을 먼저 한다.

### 서버 실행과 점검

```bash
# 같은 네트워크의 FE가 접근하려면 --host 0.0.0.0. 이 PC에서만 쓰면 생략한다(기본 127.0.0.1)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
uv run python scripts/demo_check.py     # 점검(로컬 서버만 대상)
```

- FE가 다른 PC나 다른 포트에서 호출하면 `.env`의 `CORS_ALLOWED_ORIGINS`에 그 Origin(예: `http://192.168.0.10:5173`)을 넣고 서버를 다시 시작한다.
- 방화벽에서 포트 8000을 열어야 같은 네트워크의 다른 PC가 접근한다. 인증이 없으므로 외부 인터넷에는 열지 않는다.
- 수집 데이터는 자동으로 갱신되지 않는다. 시연 직전에 `collect_elevators --apply`를 다시 실행한다(서버를 멈출 필요는 없지만 쓰는 순간 잠깐 잠길 수 있다).
- 시간표와 인접 간선은 자주 바뀌지 않으므로 한 번만 준비하면 된다.

### 시연 전 점검 목록

- [ ] `GET /health`가 `{"status":"ok"}`
- [ ] `uv run python scripts/demo_check.py`가 `FAIL=0`
- [ ] 엘리베이터 상태 조회: `GET /api/stations/{id}/elevators`
- [ ] 고장 목록: `GET /api/elevators/status?status=가동%20안됨&line=2`
- [ ] 대체역: `GET /api/stations/{id}/alternatives`
- [ ] 경로: `GET /api/routes?from={id}&to={id}`
- [ ] 출발 시각: `POST /api/departure-time` (`from_station_id`, `to_station_id` 포함)
- [ ] FE 화면에서 브라우저 콘솔에 CORS 오류가 없다

## 테스트

```bash
uv run pytest
```

테스트는 임시 SQLite 파일에 마이그레이션을 적용해서 실행하므로 `data/dev.db`를 건드리지 않는다.

## 엘리베이터 상태 TTL

- 환경 변수 `ELEVATOR_STATUS_TTL_MINUTES`(정수, 1~1440분)로 설정한다.
- 기본값은 **120분(2시간)** 이다. 갱신 주기를 검증하지 못해 정한 **임시 기본값**이며, 확인되면 바꾼다.
- 저장된 `fetched_at`이 TTL보다 오래되면 응답의 `status`는 `null`, `unknown_reason`은 `STALE`이다.

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

## 역 간선 적재 (공식 근거로 검증된 간선만)

```bash
# 기본은 dry-run: 적용해 본 뒤 롤백한다. 역 시드(load_seed)가 먼저 적재되어 있어야 한다
uv run python -m app.cli.load_edges
# 실제 반영은 --apply를 명시했을 때만 한다
uv run python -m app.cli.load_edges --apply
```

- `data/seed/station_edges_verified.csv`(VERIFIED: OA-13290 환승 간선 38개)만 읽는다.
- 인접 간선 239개는 공식 인접 증명이 없어 **PROVISIONAL**이며(`station_edges_provisional.csv`) 이 명령으로 적재되지 않는다. 적재 옵션도 없다. 검토 목록(`station_edges_review.csv`)도 적재하지 않는다.
- 공식 근거 없는 간선이 입력에 섞이면 DB에 손대기 전에 거부한다. 매핑 실패나 충돌은 전체 롤백, 재실행해도 중복되지 않고 기존 간선은 삭제·덮어쓰지 않는다.
- 등급과 한계는 `data/seed/SOURCES.md`의 "역 간선"을 본다. 간선 수는 실제 거리나 무장애 이동 가능성이 아니다.

## 시연용 데모 DB (임시 디렉터리, 운영 DB와 분리)

```bash
# 임시 디렉터리에 데모 SQLite를 만든다. 실제 data/dev.db는 읽기 전용으로 비교만 하고 쓰지 않는다
uv run python -m app.cli.build_demo_db --include-provisional-adjacent
# 출력된 demo.db로 서버 실행
DATABASE_URL=sqlite:////tmp/elevator-demo-XXXX/demo.db uv run uvicorn app.main:app
```

- 구성: 역 240 / station_lines 276(원본과 같은 매핑) + 공식 TRANSFER 38 + (옵션) PROVISIONAL ADJACENT 239 + OA-15994 샘플 EV 866 + DEMO/SYNTHETIC 합성 상태 5.
- `--include-provisional-adjacent`는 **데모 DB 전용 옵션**이다. 공식 인접 증명이 없는 간선이며, 운영용 `app.cli.load_edges`에는 이런 옵션이 없고 안전 제한은 그대로다. `--no-synthetic`이면 합성 상태를 넣지 않는다.
- **샘플 엘리베이터는 과거 응답이다(2026-11-13).** 현재 상태를 보장하지 않으며, 원본에 시각이 없어 `observed_at`은 비어 있고 `fetched_at`은 파일명 날짜의 00:00:00Z(하한값, 실제 수집 시각 아님)다. TTL(기본 120분)을 우회하지 않으므로 샘플은 **STALE(정보 없음)** 으로 보인다.
- **합성 상태는 DEMO/SYNTHETIC이다.** 실제 샘플과 별도 행이며 `source=DEMO-SYNTHETIC`, 설비명이 `[DEMO/SYNTHETIC]`로 시작한다. 생성 시각 기준이라 TTL 이후에는 STALE이 된다. 실제 상태가 아니다.
- 출력은 임시 디렉터리만 허용한다(저장소 안, 비어 있지 않은 디렉터리는 거부). `demo_manifest.json`에 구성·한계가 기록된다.
- 실제 `data/dev.db` 적재는 별도 승인 사항이다.

## 공공데이터 갱신 실행 방식 (#44)

**결정(2026-11-14): MVP에서는 수동 CLI로 실행한다.**

| 옵션 | 선택 여부 | 근거 |
|---|---|---|
| 수동 CLI | **선택** | 단순하고, 시연 PC에서 터미널로 직접 실행한다. 자동화 전에 1회 수동 검증이 필요하다 |
| GitHub Actions `workflow_dispatch` | 보조 수단 | 수동 CLI를 Actions에서 실행하는 래퍼. 정기 스케줄 없음. 기본 dry-run |
| 로컬 `cron`/`launchd`·작업 스케줄러 | 보류 | 시연 직전에 수동 실행으로 충분하다. 필요하면 Windows 작업 스케줄러로 같은 명령을 돌린다 |
| Actions `schedule` | 보류 | 호출 한도 미확인, 영속 저장소 없음(실행이 끝나면 파일이 사라진다) |

**HTTP 허용(2026-11-14):** 문서화된 주소가 HTTP뿐이어서 평문 HTTP를 기본 허용으로 바꿨다. HTTPS 연결은 개발 환경에서 TLS 핸드셰이크가 실패했고 서비스의 HTTPS 지원 여부는 단정하지 못했다. 이 저장소의 CLI는 실제 호출을 아직 실행하지 않았다. 완료 기준 "1회 실행 성공"은 시연 PC에서 `collect_elevators --apply`를 실행하면 달성된다.

**실행 이력 확인:** 수집 후 `data/logs/ingestion_issues.jsonl`에 `run_id`별로 결과가 쌓인다. `--json`으로 JSON 요약을 얻을 수 있다.

**GitHub Actions 수동 실행:** Actions → "Collect elevator data (OA-15994)" → "Run workflow". `dry_run=true`(기본)이면 DB를 바꾸지 않는다. `dry_run=false` + `DATABASE_URL` Secret 설정으로 Actions 환경의 복사본 DB에 반영할 수는 있지만, **Actions 컨테이너의 파일시스템은 임시 파일시스템이다**. 워크플로가 끝나면 모든 파일이 사라지므로, 여기서 쓴 DB는 운영 DB에 반영되지 않는다. 실제 운영 DB를 갱신하려면 영속 저장소(외부 DB, 오브젝트 스토리지 등)를 별도로 구성해야 하며 현재는 그런 구성이 없다.

---

## 엘리베이터 수집 CLI (OA-15994)

서울 열린데이터광장 OA-15994 응답에서 엘리베이터(EV)만 골라 `elevators`, `elevator_status`에 Upsert한다.

> **평문 HTTP를 기본 허용한다(팀 결정 2026-11-14).** 문서화된 주소(`http://openapi.seoul.go.kr:8088/...`)는 평문 HTTP이고 API 키가 URL에 들어간다. `--base-url`을 생략하면 이 기본 주소를 쓰고, `http://`와 `https://` 주소를 모두 받는다. 키는 `.env`에만 두고 stdout·stderr·로그·예외 메시지에는 가려서 남긴다. 사용자 정보·쿼리·프래그먼트가 든 주소는 거부한다.

### 기본은 dry-run

```bash
# DB와 로그 파일을 바꾸지 않고 무엇이 바뀔지만 보여 준다 (--apply 없음)
uv run python -m app.cli.collect_elevators

# 실제 반영은 --apply를 명시했을 때만 한다
uv run python -m app.cli.collect_elevators --apply
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
| 2 | 실행 준비 오류 (인자, 키 없음, 잘못된 주소, DB 미준비, 로그 경로 쓰기 불가, 차단된 조합) |
| 3 | 외부 API 오류 |
| 4 | DB 커밋 성공 후 로그 기록 실패 |

`--json`을 주면 결과를 JSON 하나로 출력한다. 키는 `schema_version`, `ok`, `partial_success`, `exit_code`, `run_id`, `mode`, `dry_run`, `committed`, `fetched_at`, `counts`, `unmapped_station_codes`, `log`, `warnings`, `error`이며 실패할 때도 같다.

### 주의

- 이 함수는 세션의 트랜잭션 전체를 커밋하거나 롤백한다. 다른 작업과 같은 세션을 쓰지 않는다(`app/services/elevator_ingest.py` 문서 참고).
- SQLite는 쓰기 잠금이 하나뿐이라 다른 프로세스가 쓰는 중이면 `database is locked`로 실패하고 전체가 롤백된다. 자동 재시도는 하지 않는다.
- Alembic은 같은 프로세스에서 기존 로거를 꺼 버린다. 이 명령은 로깅 설정을 바꾸지 않고 출력은 `print`로 하며, 마이그레이션과 같은 프로세스에서 실행하지 않는다.
- `elevators.station_line_id`는 없다. 설비의 노선은 `elevators.external_station_code`로 `station_external_ids`(source=`OA-15994`)를 조인해 얻는다.
