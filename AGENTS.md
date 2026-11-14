# AGENTS.md — BE 에이전트 작업 가이드

이 레포에서 작업하는 AI 코딩 에이전트(Gemini, Claude, Codex 등)가 **가장 먼저** 읽는 문서다.
사람용 소개와 실행 방법은 [README.md](README.md)에 있다. 이 문서는 README를 복제하지 않고, "무엇을 지켜야 하고 어디를 봐야 하는가"만 적는다.

> 이 문서는 `main` 5d2b26a(PR #69 머지, 2026-11-14) 기준이다. 시간표·경로 API·대기 5분·Windows 수정이 모두 `main`에 들어 있다. 이후 변경은 **PR #74(OPEN, 리뷰 중, 브랜치 `feat/be-train-selection-edge-fix`)에만 있고 `main`에는 없다**: 순환 종착 시간표 병합, 간선 지지 비율 필터와 `--prune`, 시간표 기반 열차 선택(#38, `selected_train`). **코드와 `docs/api-contract.md`가 현재 사실**이고 이 문서는 길잡이다. 다르면 코드를 믿고, 이 문서를 고친다.

## 1. 이 레포는 무엇인가

우왕톤 7팀 "엘리베이터 지하철" 서비스의 **백엔드**. 휠체어·유모차 이용자가 지하철 역의 엘리베이터 상태, 고장 시 대체역, 권장 출발 시각을 확인하게 해 준다.
Python 3.13, FastAPI, SQLite(SQLAlchemy + Alembic), 패키지 관리 `uv`.

- **FE**(`FE/`)가 이 API를 호출하고, **AI**(`AI/`, 챗봇)가 같은 API를 읽어 안내 문장의 근거로 쓴다. **AI는 이 API의 응답 형식에 직접 의존한다**(§8).
- 외부 데이터: 서울 열린데이터광장. 엘리베이터 가동 상태는 OA-15994(매시 갱신으로 알려져 있으나 미검증), 열차 시간표는 OA-101(인접 간선 추출에 사용).
- 제공하는 API: 역 검색·상세, 역별·전체 엘리베이터 상태(`line` 필터), 대체역, **경로(`GET /api/routes`, 다익스트라 최단 경로 + 경로 위 엘리베이터 경고)**, 출발 시각, 실시간 도착(외부 출처 미연동이라 항상 `UNCONFIRMED`).
- 이 레포는 정답을 만들어 내는 곳이 아니라 **확인된 데이터를 있는 그대로, 불확실하면 불확실하다고** 돌려주는 곳이다.

## 2. 읽는 순서

1. 이 문서
2. [docs/api-contract.md](docs/api-contract.md) — API 응답 계약, 상태 판정 규칙(§6), 결정 사항 D1~D18, 미검증 항목(§9). **API를 만지기 전에 필독**
3. [docs/frontend-integration.md](docs/frontend-integration.md) — FE/AI가 쓰는 API 요약
4. [docs/demo-handoff.md](docs/demo-handoff.md), [docs/deployment-considerations.md](docs/deployment-considerations.md) — 시연과 배포 조건(배포 대상은 미정)
5. [docs/api-notes.md](docs/api-notes.md) — 외부 API(OA-15994 등) 실제 응답 조사 기록
6. [data/seed/SOURCES.md](data/seed/SOURCES.md) — 역·노선·간선 시드의 출처와 커버리지

## 3. 반드시 지킬 원칙

이 서비스에서 틀린 정보는 이용자가 고장 난 엘리베이터에 갇히거나 열차를 놓치는 결과로 이어진다.

1. **정상으로 추정하지 않는다.** 상태 행이 없거나 TTL을 넘은 설비는 `가동중`으로 채우지 않는다. `status: null`과 `unknown_reason`(`NO_DATA`/`STALE`)으로 돌려준다 (계약 §6.3).
2. **불확실성은 `limitations`로 알린다.** 업무 응답에는 `limitations`가 항상 있다(없으면 `[]`). 표준 문구는 Backend 상수(`app/services/status_messages.py`)로 관리하고 계약 §0.4 표와 같이 바꾼다.
3. **값을 지어내지 않는다.** 좌표·환승·간선·시간표·실시간 도착은 근거가 있는 것만 넣고, 없으면 `null`·빈 배열·`UNCONFIRMED`로 둔다. 공식 인접 증명이 없는 간선은 `PROVISIONAL`이며 운영 적재 경로(`load_edges`)가 막는다. 시간표에서 추출한 인접 간선(`derive_edges`, source `OA-101`)은 공식 인접역 자료가 아니라는 점을 `limitations`로 알린다.
4. **추정값은 `estimated: true`로 표시한다.** 경로·출발 시각처럼 계산한 값에는 추정이라고 밝히고 `limitations`를 붙인다. 경로 소요 시간(역 간 2분, 환승 5분)과 출발 시각의 도보·진입·출구·대기(7·3·3·5분)는 **검증되지 않은 가정**이다.
5. **API 응답 형식은 FE/AI와의 계약이다.** 필드·값·문구를 바꾸면 `docs/api-contract.md`(그리고 필요하면 `docs/frontend-integration.md`)를 같이 고치고, **AI 쪽에 영향이 있다고 PR 본문에 적는다**(§8).
6. **비밀과 운영 데이터를 지킨다.** API 키(`SEOUL_OPEN_API_KEY`)를 코드·로그·문서·채팅에 쓰지 않는다. `.env`는 커밋하지 않는다. `data/dev.db`(운영 DB)에는 쓰지 않으며 실제 적재는 **별도 승인 사항**이다.
7. **시연·실서비스에서 Mock 데이터를 쓰지 않는다**(대회 규칙). 개발용 테스트 데이터는 괜찮다. 데모 DB의 합성 상태(`[DEMO/SYNTHETIC]` 5건)가 이 규칙에 걸리는지는 **팀 확인이 필요하다**(§7).

## 4. 명령어

```bash
uv sync                                   # 의존성 설치 (.venv)
uv run pytest                             # 전체 테스트. 작업 전후로 반드시 실행. 임시 SQLite를 쓰므로 data/dev.db를 건드리지 않는다
uv run alembic upgrade head               # 마이그레이션 적용 (data/dev.db)
uv run uvicorn app.main:app --reload      # 서버 (http://127.0.0.1:8000, /docs = Swagger UI)

# 연동 시연용: 운영 DB와 분리된 데모 DB (README "FE/AI 로컬 연동 빠른 시작")
uv run python -m app.cli.build_demo_db --include-provisional-adjacent
DATABASE_URL=sqlite:///<출력된 절대경로>/demo.db uv run uvicorn app.main:app --port 8000
uv run python scripts/demo_check.py       # 서버 점검. WARN(정보 없음 등)은 정상이다

# 데이터 작업 (실제 DB 적재는 별도 승인. 대부분 --dry-run/기본 dry-run이 있다 — 각 CLI의 --help와 README 확인)
uv run python -m app.cli.load_seed --dry-run          # 역·노선 시드
uv run python -m app.cli.collect_elevators --help     # 엘리베이터 수집 (OA-15994)
uv run python -m app.cli.collect_timetable --help     # 열차 시간표 수집 (OA-101)
uv run python -m app.cli.derive_edges --help          # 시간표에서 인접 간선 추출
```

## 5. 코드 지도 — 무엇을 바꾸려면 어디를 보는가

| 바꾸려는 것 | 파일 | 같이 고칠 것 |
|---|---|---|
| 엔드포인트 | `app/routers/*.py`, `app/main.py`(라우터 등록) | `app/schemas/*.py`, `docs/api-contract.md`, `docs/frontend-integration.md` |
| 응답·요청 모델 | `app/schemas/*.py` | 계약 문서의 필드 표·JSON 예시, 관련 테스트 |
| 업무 로직 | `app/services/*.py` | 아래 서비스 설명 참고 |
| 상태 판정(AVAILABLE/PARTIAL/UNAVAILABLE/null, TTL) | `app/services/status_judgment.py`, `app/ttl_limits.py` | 계약 §6, `tests/test_status_judgment.py` |
| limitations 표준 문구 | `app/services/status_messages.py` | 계약 §0.4 표, 관련 API 테스트 (**AI가 문구를 그대로 전달한다**) |
| 오류 응답 형식 | `app/errors.py`, `app/schemas/errors.py` | 계약 §0.6. 업무 오류는 `AppError(status, code, message)`를 던진다 |
| 설정·환경 변수 | `app/config.py`, `.env.example` | README, `docs/deployment-considerations.md` §4 |
| 출발 시각 기본값 | `app/departure_defaults.py`, `app/config.py` | 계약 §8.2, `tests/test_departure_time*.py`. 기본: 도보 7·진입 3·출구 3·대기(buffer) **5**분 (D15) |
| 경로 탐색·경고 | `app/services/routing.py`(다익스트라), `route_service.py`, `route_warning.py`, `app/routers/routes.py`, `app/schemas/routes.py` | 계약 §8.4, `tests/test_routes_api.py`. 가중치 `ROUTE_SEGMENT_MINUTES`(2)·`ROUTE_TRANSFER_MINUTES`(5)는 설정 |
| 열차 시간표 | `app/models/train_schedules.py`, `app/services/timetable.py`·`timetable_ingest.py`, `app/cli/collect_timetable.py` | `alembic/versions/c4a91f27e6b3_*`, `tests/test_timetable.py`. 시각은 서비스일 기준 정수 초, 24시 넘는 시각 변환 |
| 인접 간선 추출 | `app/services/edge_derive.py`, `app/cli/derive_edges.py` | 계약 §8.4·§9, `data/seed/SOURCES.md`. 지지 비율 필터(`MIN_RELATIVE_SUPPORT` 20%)와 `--prune`은 PR #74(머지 전) |
| 열차 선택(출발 시각) | `app/services/train_selection.py`(PR #74, 머지 전) | 계약 §0.4·§8.2, `tests/test_train_selection.py`. 방향은 정차 순서로 추정하며 근거가 없으면 기존 추정으로 폴백 |
| DB 모델 | `app/models/*.py` | **Alembic 마이그레이션**(`alembic/versions/`), `tests/test_schema.py` |
| 역·노선·간선 시드 | `data/seed/`, `app/services/station_seed.py`·`edge_seed.py`, `app/cli/load_*.py` | `data/seed/SOURCES.md`, 시드 테스트. **원본 CSV 해시는 `manifest.json`에 있다** |
| 외부 API 클라이언트·파서 | `app/clients/` | `docs/api-notes.md`, 보안 테스트(키 노출·평문 HTTP 차단) |
| 엘리베이터 수집 | `app/cli/collect_elevators.py`, `app/services/elevator_ingest.py`, `.github/workflows/collect-elevators.yml` | README "공공데이터 갱신 실행 방식" |
| 데모 DB | `app/services/demo_db.py`, `app/cli/build_demo_db.py` | `docs/demo-handoff.md`, `scripts/demo_check.py` |

**서비스 계층** (`app/services/`): `stations`(검색·상세), `elevator_queries`·`status_judgment`·`status_messages`(상태 조회·판정·문구), `alternatives`(대체역 BFS), `departure_time`(출발 시각 역산, 순수 함수), `arrivals`(실시간 도착, 메모리 TTL 캐시), `edge_*`(간선), `*_seed`(시드 적재), `elevator_ingest`·`ingestion_log`(수집·이력), `demo_db`.

**테스트** (`tests/`): `conftest.py`의 `engine`·`db` 픽스처가 **임시 SQLite에 마이그레이션을 적용**한다. 역 API 테스트 데이터(`STATION_FIXTURE`)는 실제 역 마스터가 아니라 규칙 확인용이다.

## 6. 작업 규칙

- **동작을 바꾸면 테스트를 추가하고, API 응답이 바뀌면 계약 문서를 고친다.** 문서에 없는 동작을 만들지 않는다.
- **실행해서 확인하지 않은 것을 "완료", "정상 동작"이라고 쓰지 않는다.** 테스트 수·응답 값은 실제로 돌린 값만 적는다. 확인하지 못한 것은 `(미검증)`으로 적는다(문서 전체의 관례다).
- DB 스키마를 바꾸면 **Alembic 마이그레이션을 추가**하고 `tests/test_schema.py`가 통과해야 한다. 이미 머지된 마이그레이션을 고치지 않는다.
- **시각 규칙**: 저장은 UTC(`...Z`, 초 단위), 응답은 KST(`+09:00`). 변환은 `app/timeutil.py`의 함수를 쓴다. 요청의 시각은 오프셋이 있어야 한다.
- 필드 이름은 `snake_case`. 모든 업무 API는 `/api` 아래(`/health`만 예외). 검색 결과 0건은 404가 아니라 200과 빈 배열.
- 외부 API는 **서버 요청 처리 중에 호출하지 않는다.** 수집은 CLI/Actions가 한다. 서울 열린데이터광장은 문서화된 주소가 HTTP(8088)뿐이라 **평문 HTTP를 기본 허용**한다(D14, 2026-11-14). 키는 서버 `.env`에만 두고 로그·오류에는 가린다(`tests/test_seoul_open_api_security.py`).
- MVP 범위에서 Docker, Redis, PostgreSQL은 도입하지 않는다(Issue #45).
- 문서·주석·응답 문구는 **한국어**, 파일은 **UTF-8**. 외부에 보이는 `limitations`·오류 메시지는 FE가 그대로 보여 줄 수 있는 문장으로 쓴다.
- **커밋 메시지**: `feat:`, `fix:`, `docs:`, `test:`, `refactor:` 접두어 + 영어 한 줄 요약(예: `feat: Add alternative station API`), 이슈가 있으면 `(#44)`. 지금까지의 이력이 이 형식이다.
- **PR**은 `.github/pull_request_template.md`(Changes / Testing / Related Issue)로 쓰고, 이슈는 `Closes #N`·`Refs #N`으로 연결한다. 테스트는 실제 실행 결과를 적는다.
- GitHub Issue 본문 형식: 첫 줄 `우선순위: P0|P1|…`, `### 의존성` 목록. Notion 연동 workflow(`.github/workflows/notion-*.yml`)가 이 형식에 의존하므로 임의로 바꾸지 않는다.

## 7. 아직 결정되지 않은 것 (임의로 정하지 말고 사용자에게 묻는다)

- **갱신 주기·TTL**: 현재 120분은 임시값이다(D1). OA-15994 갱신 주기를 확인한 뒤 확정한다.
- **역 전체 목록 API**: AI가 요청 중이다(BE #14 코멘트). `GET /api/stations`는 `q`가 필수라 전체 목록을 받을 수 없어, AI는 `GET /api/stations/{id}`를 ID 1부터 차례로 조회하는 우회를 쓴다(ID가 연속이라는 가정에 기댐). `q` 생략 허용이나 별도 목록 API를 검토한다. **5d2b26a 기준 `main`에는 아직 반영되지 않았다.**
- **데이터 현황 (2026-11-14, `data/dev.db`, 사용자 승인 후 적재)**: 시드 역 240개·환승 간선 38개, 엘리베이터 866건 수집, 시간표(OA-101) 전체 수집 279,458행, 시간표에서 추출한 인접 간선 269개(`--prune` 후). 순환 종착 병합 1,142건. 합성 상태 5건이 남아 있는지는 (미검증). 시간표는 공휴일 데이터가 없어 평일·토·일만 구분한다. 시간표 수집 276개는 역-노선 쌍이고 시드 240개는 고유 역이라(환승역 34개가 36쌍을 줄임) 불일치가 아니다(#16, `docs/api-notes.md`).
- **경로·간선의 신뢰도**: 경로의 인접 간선은 시간표(OA-101)에서 추출한 것이고 공식 인접역 자료가 아니며, 소요 시간은 역 간 2분·환승 5분 가정이다. 엘리베이터만으로 이동 가능한 환승·출입 동선인지도 검증되지 않았다. 시연에서 경로를 어떻게 표현할지 팀 확인이 필요하다.
- 팀 결정이 끝난 것(참고): `buffer_minutes` 기본 5분(D15), 서울 열린데이터광장 평문 HTTP 허용(D14), 인접 간선을 시간표에서 추출(D16), 상태 목록의 `line` 필터(D18).
- **시연 데이터와 Mock 금지**: 데모 DB의 `[DEMO/SYNTHETIC]` 상태 5건을 시연에서 써도 되는지, 쓸 수 없다면 **실제 엘리베이터 수집(#44)을 시연 전에 돌려야 한다**. 실제 수집은 OA-15994 HTTPS 주소 확인과 호출 한도 확인이 선행된다.
- **배포 대상**(호스팅, HTTPS, SQLite 영속 디스크, 백업)과 실제 `data/dev.db` 적재 — 모두 별도 승인 사항이다(`docs/deployment-considerations.md` §5).
- 계약 문서의 FE/AI 리뷰 확정(BE #14는 리뷰 전까지 Open), `observed_at`(원본에 시각이 없어 항상 `null`), 역 좌표 출처, 1호선 전체 구간 확보.

## 8. 다른 파트와의 관계

| 대상 | 관계 |
|---|---|
| **AI 레포**(`../AI`) | AI의 `src/data/be_source.py`가 이 API를 호출한다. 현재 읽는 API: `GET /api/stations/{id}`, `/api/stations/{id}/elevators`, `/api/elevators/status`, `/api/stations/{id}/alternatives`, `POST /api/departure-time`. **`GET /api/routes`와 출발 시각의 `from/to` 역 id, 상태 목록의 `line` 필터는 BE에 새로 생겼고 AI는 아직 연동 전이다**(AI 이슈 #6, #12에서 진행 예정). **응답 필드명·상태 값·limitations 문구를 바꾸면 AI의 변환 코드와 테스트 픽스처(`AI/tests/data/fixtures/`)가 깨질 수 있다.** 바꿀 때 PR 본문에 "AI 영향"을 적는다. AI는 `limitations`를 사용자에게 그대로 전달한다. PR #74 머지 후에는 출발 시각 응답에 `selected_train`(기본 `null`)이 추가되고 `timetable_used`가 `true`일 수 있다. AI는 이 필드를 읽지 않지만 `from/to`를 보내지 않아 지금은 켜지지 않는다. AI의 가이드: `../AI/AGENTS.md`, 연동 현황: `../AI/docs/IMPLEMENTATION_STATUS.md` §4.8 |
| **FE** | `docs/frontend-integration.md`가 기준. CORS는 `CORS_ALLOWED_ORIGINS`에 FE 주소를 넣어야 열린다 |
| 공용 문서 | `../docs/PROJECT-STATE.md`(프로젝트 전체 진행 상황, git 저장소 밖) |

- AI가 BE에서 쓰는 가정: 역 ID는 정수, 괄호 부제가 있는 역명(`잠실(송파구청)`)은 AI가 괄호를 뗀 기본 역명으로 비교한다, 설비 `status`는 `가동중`/`가동 안됨`/`null`.

## 9. 알려진 환경 문제 (Windows)

- Windows에는 시간대 DB가 없어 `ZoneInfo("Asia/Seoul")`가 실패한다. **`tzdata` 패키지가 의존성에 있어야 한다**(`pyproject.toml`). 없으면 서버가 시작되지 않는다. *(da72d68에서 의존성으로 추가됨)*
- `alembic.ini`에 한글이 들어 있으면 cp949 로캘에서 `UnicodeDecodeError`가 난다(alembic과 `logging.config.fileConfig`가 로캘 인코딩으로 읽음). **`alembic.ini`는 ASCII로만 쓴다.** *(da72d68에서 ASCII로 바뀜)*
- Windows에서는 심볼릭 링크·`chmod` 테스트가 건너뛰어진다(정상).
- Git Bash의 `curl`은 한글 쿼리를 깨뜨린다. `--data-urlencode`를 쓰거나 Python `httpx`로 호출한다.
- 터미널 출력이 깨져 보이면 파일로 저장해서 UTF-8로 읽는다.

## 10. 여러 에이전트가 같은 폴더에서 일할 때

- 이 작업 폴더는 **다른 사람·에이전트가 동시에 작업 중일 수 있다.** 시작하기 전에 `git status`와 `git branch --show-current`를 확인한다.
- **내가 만들지 않은 수정·untracked 파일은 건드리지 않는다.** 되돌리거나 덮어쓰거나, 내 커밋에 섞지 않는다. `git add -A`·`git add .` 대신 **내가 고친 파일만 이름으로 add**한다.
- `main`에 직접 커밋하지 않는다. 브랜치를 만들어 작업하고 PR로 올린다(`feat/…`, `fix/…`). 지금 작업 폴더가 어느 브랜치인지(`git branch --show-current`)와 그 브랜치가 `main`에 머지됐는지 먼저 확인한다.
- 서로 다른 이슈는 서로 다른 브랜치로. 같은 파일(특히 `docs/api-contract.md`, `app/config.py`, `app/main.py`)을 여러 사람이 고칠 수 있으니 PR 전에 `git fetch` 후 최신 `main`과 충돌을 확인한다.
- 되돌릴 수 없는 작업(force push, `reset --hard`, 운영 DB 쓰기, 외부 API 실제 호출)은 사용자 확인 없이 하지 않는다.

## 11. 작업 전 체크리스트

1. `git status`로 현재 상태 확인, 관련 이슈(`gh issue view N`)와 `docs/api-contract.md` 해당 절 읽기
2. 바꿀 파일이 §5 표의 "같이 고칠 것"에 어떤 것을 끌고 오는지 확인
3. 구현 + 테스트 추가, `uv run pytest` 통과
4. API가 바뀌면 계약 문서 갱신, AI 영향 여부 PR 본문에 기록
5. 값·동작을 문서에 쓸 때는 실제로 실행해서 확인한 것만 적기
