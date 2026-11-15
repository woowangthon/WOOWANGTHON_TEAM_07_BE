# WOOWANGTHON_TEAM_07_BE

지하철 엘리베이터 접근성 안내 및 출발 시각 추천 서비스 백엔드. Python 3.13, FastAPI, SQLite(SQLAlchemy + Alembic).

## 실행 방법

### Google·카카오 로그인 준비

코드는 인증 키 없이도 실행된다. 키가 없는 제공자의 로그인 요청은 503 `AUTH_NOT_CONFIGURED`를 반환하며 데모 인증으로 바뀌지 않는다.

1. 기존 `BE/.env`에 아래 네 값을 추가한다. 이미 있는 `.env`를 덮어쓰지 않는다.

   ```dotenv
   GOOGLE_CLIENT_ID=발급받은_웹_클라이언트_ID
   GOOGLE_CLIENT_SECRET=발급받은_클라이언트_시크릿
   KAKAO_CLIENT_ID=발급받은_REST_API_키
   KAKAO_CLIENT_SECRET=발급받은_클라이언트_시크릿
   CORS_ALLOWED_ORIGINS=http://127.0.0.1:5173
   ```

2. Google 웹 애플리케이션 OAuth 클라이언트에 승인된 리디렉션 URI `http://127.0.0.1:8000/api/auth/google/callback`을 등록한다. OAuth 동의 화면을 설정하고 테스트 모드라면 사용할 Google 계정을 테스트 사용자로 추가한다.
3. 카카오 앱의 카카오 로그인을 활성화하고 Redirect URI `http://127.0.0.1:8000/api/auth/kakao/callback`을 등록한다. `KAKAO_CLIENT_ID`에는 REST API 키를 사용한다. Client Secret 기능이 켜져 있으면 `KAKAO_CLIENT_SECRET`이 필수다. 닉네임 제공에 동의하지 않아도 기본 이름으로 로그인할 수 있다.
4. 대상 DB를 백업한 뒤 마이그레이션을 적용하고 서버를 시작한다. 아래 명령은 **직접 실행할 때 설정된 DB를 변경**한다. 이번 구현 검증에서는 임시 DB만 사용했다.

   ```powershell
   # BE 폴더
   uv run alembic upgrade head
   uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
   # 다른 터미널, FE/web 폴더
   npm.cmd run dev:auth
   ```

`http://127.0.0.1:5173/#/main`에서 로그인 탭을 연다. 일반 `npm.cmd run dev`와 `dev:auth` 모두 실제 인증을 사용한다. 데모 인증은 `dev:mock`으로 명시해서 실행한다. 제공자 키가 설정되어 있으면 `AUTH_FRONTEND_URL`에 지정된 정확한 Origin은 쿠키 요청에 자동 허용하며, 추가 Origin은 `CORS_ALLOWED_ORIGINS`로 지정한다. 로그인한 뒤 이름·즐겨찾기 역/장소를 저장하면 서버 DB에서 새로고침·재로그인 때 복원한다. 로그아웃은 세션만 폐기하고 즐겨찾기는 남긴다. 역 추천은 저장한 역과 확인된 역 ID만 사용한다. 역 목록이 없는 새 DB에서는 역 시드 적재가 별도로 필요하다.

`GET /api/auth/providers`는 키 값을 공개하지 않고 제공자별 `configured`와 `storage_ready`만 반환한다. 키를 입력했는데 저장소가 준비되지 않았다면 DB 마이그레이션이 필요하다. 미적용 DB에서 로그인하면 500 대신 503 `AUTH_STORAGE_NOT_READY`를 반환하며 DB를 자동 변경하지 않는다. `.env` 키를 추가/변경한 뒤에는 BE 서버를 재시작한다.

로그인은 유지되지만 콜백에서 `ERR_INVALID_REDIRECT`가 발생하면 로그인 후 이동 주소 `AUTH_FRONTEND_URL`을 확인한다. 로컬 기본값은 `http://127.0.0.1:5173/#/main`이다. 주소·포트 내부의 공백이나 제어 문자, 숫자가 아니거나 범위를 벗어난 포트는 서버 시작 시 거부한다. 이 값은 제공자 콘솔에 등록할 BE 콜백 URI와 구분한다. 브라우저의 콜백 주소에 붙은 일회용 `code`·`state`를 복사해 재사용하지 말고 서비스의 로그인 버튼에서 다시 시작한다.

카카오 로그인은 REST API 인가 코드 → 서버 토큰 교환 → `/v2/user/me` 조회 → 계정별 세션 발급 순서다. 닉네임은 동의 설정에 따라 `properties.nickname` 또는 `kakao_account.profile.nickname`을 읽으며, 둘 다 없으면 기본 이름 `사용자`로 로그인한다. 닉네임·이메일 동의를 필수로 추가하지 않는다. Secret을 사용하는 앱은 `KAKAO_CLIENT_SECRET`도 입력한다. 카카오 콘솔에는 로그인 활성화와 `http://127.0.0.1:8000/api/auth/kakao/callback` 등록이 필요하다.

실제 카카오 진입 검증은 FE/web에서 `node scripts/verify-kakao-live.mjs`로 실행한다. 새 브라우저에서 서비스의 카카오 버튼을 눌러 입력 화면 또는 표준 `KOE...` 오류만 확인하며, 키·인증 URL·쿠키·비밀번호를 출력하지 않는다. `KOE004`는 카카오 로그인 활성화, `KOE006`은 Redirect URI 설정을 확인한다. 로그인 성공 후 서비스 복귀·사용자 정보·즐겨찾기 복원은 사용자가 직접 로그인·동의한 뒤 확인한다.

자동 테스트는 제공자의 토큰·사용자 정보 HTTP 응답을 대체해 OAuth 흐름과 DB 저장을 검증하며 실제 Google·카카오에 접속하지 않는다. 별도의 `FE/web/scripts/verify-google-live.mjs`와 `verify-kakao-live.mjs`로 실제 키·콜백 등록 후 두 제공자의 계정 입력 화면 진입도 확인했다. Google은 사용자 로그인 성공도 확인했다. 카카오 사용자 비밀번호·동의 입력과 인증 완료는 직접 로그인 후 확인한다. 서버는 외부 제공자와 통신할 수 있는 실행 환경에서 구동해야 한다.

배포 시 `AUTH_PUBLIC_BASE_URL=https://백엔드주소`, `AUTH_FRONTEND_URL=https://프론트주소/#/main`, `AUTH_COOKIE_SECURE=true`, `CORS_ALLOWED_ORIGINS=https://프론트주소`로 설정하고 콘솔의 콜백 URI도 같은 값으로 바꾼다. FE의 `VITE_API_BASE`·`VITE_BE_BASE`에는 공개 BE 주소만 넣고 `npm.cmd run build:auth`로 빌드한다. 쿠키는 SameSite=Lax이므로 FE/BE는 같은 사이트의 HTTPS 도메인 또는 같은 Origin의 프록시로 배치한다. 서로 다른 사이트를 연결하는 쿠키 구성은 지원하지 않는다. 로컬에서도 `localhost`와 `127.0.0.1`을 섞지 않는다.

세션 쿠키·OAuth 코드가 접근 로그에 남지 않도록 인증 서버 실행 예시는 `--no-access-log`를 사용한다. 역·시간표 수집 API는 기존 방식대로 유지하며, 로그인 라우터만 OAuth 코드 교환과 사용자 정보 조회를 외부 제공자에 요청한다.

공식 설정 기준: [Google 웹 서버 OAuth](https://developers.google.com/identity/protocols/oauth2/web-server), [카카오 로그인 REST API](https://developers.kakao.com/docs/ko/kakaologin/rest-api).

### 기본 서버 실행

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
- `collect_timetable`은 응답의 `LINE_NUM`이 없거나 1~8호선이 아니거나 연결된 노선과 다른 행이 하나라도 있으면 **전체 적재를 거부하고**(종료 코드 1) 기존 데이터를 유지한다(#71). 저장된 그룹보다 오래된 `fetched_at`으로는 교체하지 않으며(같은 시각 재실행은 허용), `fetched_at`은 UTC 초 단위 `...Z`로 저장한다. 실제 적재 전에는 dry-run으로 거부 여부를 먼저 확인한다.
- `derive_edges`는 시간표에서 같은 열차의 정차 순서로 인접역을 뽑는다(급행 제외, 2대 이상 확인, 시간 차 20분 이하). 공식 인접역 자료가 아니므로 경로 응답의 `limitations`에 그렇게 표시된다.
- 이 시드·간선·시간표 적재는 **실제 `data/dev.db`를 바꾼다.** 위 백업을 먼저 한다.

### 서버 실행과 점검

장시간 실행에는 포트·DB 버전을 먼저 점검하는 다음 명령을 권장한다.

```powershell
uv run python -m app.cli.serve --port 8000
# 실제 외부 API 수집·DB 적재를 승인하고 자동 갱신할 경우:
uv run python -m app.cli.serve --port 8000 --refresh-elevators
```

- 자동 갱신은 기본 OFF다. `--refresh-elevators` 또는 `.env`의 `ELEVATOR_AUTO_REFRESH=true`로 켜면 시작 직후 1회, 이후 기본 30분마다 기존 수집 CLI를 별도 프로세스로 실행한다. `SEOUL_OPEN_API_KEY`가 필요하다. `ELEVATOR_REFRESH_INTERVAL_MINUTES`(1~60)는 TTL보다 짧아야 한다. 갱신 주기와 호출 한도는 아직 미검증이다.
- 자동 수집은 **서버 한 개에서만** 켠다. `serve`는 단일 프로세스 실행이며 reload/workers 옵션을 제공하지 않는다. 기존 uvicorn 명령에서도 환경 변수로 활성화할 수 있지만 여러 worker에서 켜면 중복 수집한다.
- 수집 실패는 API 서버를 중단하지 않는다. 실패 후 1분, 2분, 4분 순으로 재시도 간격을 늘리고 정상 수집 주기까지만 늘린다. 5분을 넘긴 수집은 종료한다. 수집 CLI의 트랜잭션·부분 성공 규칙은 아래와 같다. 기존 데이터의 TTL을 연장하지 않으며 오래된 상태는 계속 `STALE`이다. 종료 코드 4는 DB 반영 후 로그만 실패한 경우다.
- `GET /health`는 프로세스 생존, `GET /ready`는 DB 연결·스키마 버전과 상태 데이터의 최신/오래된 건수를 확인한다. 데이터가 없어도 또는 전부 오래돼도 `/ready`는 200이며, DB 오류·미적용 마이그레이션은 503이다. `/ready`는 외부 API를 호출하지 않는다.
- SQLite 연결은 쓰기 잠금 해제를 최대 30초 기다린다. 장시간 잠기면 실패하며 수집기는 다음 재시도에서 복구를 시도한다. 마이그레이션과 시드 적재는 자동 실행하지 않는다. 기존 DB는 변경 전 백업한다.
- PC가 절전·종료되거나 터미널을 닫으면 갱신도 멈춘다. PC 재개 후 이벤트 루프가 다시 실행되면 수집을 재개하지만, OS 부팅 시 자동 시작이나 서버 비정상 종료 후 재시작은 제공하지 않는다.

`WinError 10048`은 DB 오류가 아니라 포트 중복 사용이다. PowerShell에서 다음과 같이 확인한다. 기존 프로세스가 이 백엔드인지 확인한 뒤 해당 터미널의 Ctrl+C로 종료하고 다시 실행한다.

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object LocalAddress, LocalPort, OwningProcess
# 위에서 확인한 PID를 숫자로 넣는다.
Get-Process -Id <PID>
```

포트를 변경하면 FE/AI API 주소와 OAuth 콜백 주소도 함께 맞춰야 한다. `serve`는 기존 프로세스를 자동 종료하지 않으며 포트를 확보하지 못하면 DB 점검·자동 수집 전에 종료한다. DB가 준비되지 않았다면 백업 후 `uv run alembic upgrade head`를 별도로 실행한다.

```bash
# 같은 네트워크의 FE가 접근하려면 --host 0.0.0.0. 이 PC에서만 쓰면 생략한다(기본 127.0.0.1)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
uv run python scripts/demo_check.py     # 점검(로컬 서버만 대상)
```

- FE가 다른 PC나 다른 포트에서 호출하면 `.env`의 `CORS_ALLOWED_ORIGINS`에 그 Origin(예: `http://192.168.0.10:5173`)을 넣고 서버를 다시 시작한다.
- 방화벽에서 포트 8000을 열어야 같은 네트워크의 다른 PC가 접근한다. 역·경로 API는 공개 읽기 API이며, 외부 인터넷 배포는 HTTPS·접근 제어 등 배포 조건을 갖춘 뒤 진행한다. 실제 로그인은 위 OAuth 설정을 따른다.
- 자동 갱신을 켜지 않았다면 시연 직전에 `collect_elevators --apply`를 다시 실행한다(서버를 멈출 필요는 없지만 쓰는 순간 잠깐 잠길 수 있다).
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

2026-11-15 추가: 장시간 실행을 위해 위 `app.cli.serve --refresh-elevators`가 기존 CLI를 정기 실행하는 선택 기능을 추가했다. 기본은 수동이며 실제 수집 활성화는 운영자가 명시한다. GitHub Actions의 임시 DB를 갱신하는 방식과 달리 서버 PC의 DB에 적용한다.

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
- SQLite는 쓰기 잠금이 하나뿐이라 다른 프로세스가 쓰는 중이면 최대 30초 기다리고 `database is locked`로 실패하면 전체가 롤백된다. 일회성 CLI 자체는 자동 재시도하지 않는다. 선택형 자동 수집기는 실패한 CLI를 백오프 후 다시 실행한다.
- Alembic은 같은 프로세스에서 기존 로거를 꺼 버린다. 이 명령은 로깅 설정을 바꾸지 않고 출력은 `print`로 하며, 마이그레이션과 같은 프로세스에서 실행하지 않는다.
- `elevators.station_line_id`는 없다. 설비의 노선은 `elevators.external_station_code`로 `station_external_ids`(source=`OA-15994`)를 조인해 얻는다.
