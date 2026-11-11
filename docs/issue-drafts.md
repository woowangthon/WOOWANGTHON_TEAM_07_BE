# WOOWANGTHON TEAM 07 — BE 이슈 초안 (승인 전)

> 상태: **초안. 아직 GitHub Issue를 만들지 않았다.** 승인 후 순서대로 `gh`로 생성한다.
> 기준: HANDOFF.md 11번(작성 규칙), 12번(합의된 17개 계획).

## 0. 공통 규칙
- Issue Type: Feature = 서비스 기능, Task = 환경·DB·문서·조사·연동 점검, Bug = 실제 오류. **Labels는 쓰지 않는다.**
- Issue Type `Task`는 저장소에서 지원됨을 확인했다. `Feature`, `Bug`, `Docs`는 생성 시점에 확인한다.
- 본문 첫 줄은 `우선순위: P0 | P1 | P2` 형식이다. 0번 Issue가 완료되면 이 줄이 Notion "우선순위"(select)에 자동 반영된다. **0번 완료 전까지 Notion 우선순위는 수동 입력이다.**
- 브랜치는 `feat/be` 하나를 쓴다. 이슈는 순서대로 하나씩 처리하고 동시에 진행하지 않는다.
- PR은 `feat/be` → `main`으로 만들고 본문에 `Closes #N`을 쓴다. 병합은 **Create a merge commit**(Squash 금지)이다.
- 체크박스 하나 완료 = 커밋 하나 + 푸시 하나. 푸시 때마다 Issue 체크박스를 갱신한다.
- 본문의 `순서 N`은 생성 후 `#번호`로 바꾼다.
- 미검증 항목은 `(미검증)`으로 표시한다.

## 1. 요약 표
| 순서 | 제목 | Type | 우선순위 | 의존성 | 승인 필요 |
|---|---|---|---|---|---|
| 0 | Notion 우선순위 자동 동기화 | Task | P1 | 없음 | **예 (`.github` 수정)** |
| 1 | FastAPI 프로젝트 초기 환경 구축 | Task | P0 | 없음 | 예 |
| 2 | FE/AI API 계약 초안 작성 및 합의 | Task | P0 | 순서 1 | 예 |
| 3 | 공공데이터 API 응답 구조 및 매핑 검증 | Task | P0 | 순서 1 | 예 |
| 4 | SQLite·SQLAlchemy·Alembic 구성 | Task | P0 | 순서 1 | 예 |
| 5 | 역·노선 모델 및 초기 데이터 적재 | Feature | P0 | 순서 3, 4 | 예 |
| 6 | 역 검색 및 상세 조회 API 구현 | Feature | P0 | 순서 2, 5 | 예 |
| 7 | 엘리베이터 데이터 수집 및 저장 | Feature | P0 | 순서 3, 5 | 예 |
| 8 | 역별 엘리베이터 상태 판정 로직 구현 | Feature | P0 | 순서 4 | 예 |
| 9 | 엘리베이터 목록 및 운행 상태 조회 API 구현 | Feature | P0 | 순서 2, 7, 8 | 예 |
| 10 | 역 인접 그래프(station_edges) 구축 | Feature | P1 | 순서 3, 5 | 예 |
| 11 | 엘리베이터 고장 시 대체역 추천 API 구현 | Feature | P1 | 순서 9, 10 | 예 |
| 12 | 정규 열차 시간표 수집 및 조회 구현 | Feature | P1 | 순서 3, 5 | 예 |
| 13 | 지하철 경로 탐색 API 구현 | Feature | P1 | 순서 2, 9, 10 | 예 |
| 14 | 출발 시각 역산 API 구현 | Feature | P1 | 순서 12, 13 | 예 |
| 15 | 실시간 열차 도착정보 연동 | Feature | P2 | 순서 3, 5 | 예 |
| 16 | FE/AI 연동 점검 및 통합 테스트 | Task | P1 | 순서 9, 11, 13, 14 | 예 |

의존 흐름: 1 → (2, 3, 4) → 5 → (6, 7, 10, 12). 7 + 8 → 9. 9 + 10 → 11, 13. 12 + 13 → 14. 16은 마지막이다.

---

## 순서 0. Notion 우선순위 자동 동기화
- **Type:** Task / **우선순위:** P1 / **의존성:** 없음
- **주의:** `.github/workflows/notion-issues-sync.yml`을 수정한다. 사용자 승인 후에만 진행한다. 다른 workflow 두 개는 건드리지 않는다.

```
우선순위: P1

### 작업 내용
- [ ] 본문 첫 번째 비어 있지 않은 줄에서 `우선순위: P0|P1|P2`를 파싱하는 함수 추가 (정규식 `^우선순위:\s*(P[0-2])\s*$`)
- [ ] 파싱 함수 단위 검증: P0/P1/P2, 줄 없음, 잘못된 값(P3 등), 공백 포함 케이스
- [ ] opened 경로에서 Notion "우선순위" select 속성 입력 추가
- [ ] edited 경로에서 우선순위 갱신 추가 (제목·설명 동기화와 같은 분기)
- [ ] 우선순위 줄이 없거나 잘못된 값이면 Notion 속성을 바꾸지 않고 로그만 출력
- [ ] typed / untyped 경로는 기존 동작(업무 유형만 갱신) 유지
- [ ] 테스트 Issue 1개로 opened → edited(우선순위 변경) 실제 동작 확인 후 결과 기록
- [ ] 테스트 Issue 정리 (Notion 항목 포함, 정리 방법 기록)

### 완료 기준
- [ ] 실제 Issue 생성·수정 시 Notion "우선순위"가 P0/P1/P2로 반영된다
- [ ] 우선순위 줄이 없으면 기존 Notion 값이 유지된다
- [ ] 기존 제목·설명·업무 유형 동기화가 깨지지 않았다
- [ ] Notion select 옵션 자동 생성 여부 `(미검증)` 또는 확인 결과 기록

### 의존성
- 없음
```

---

## 순서 1. FastAPI 프로젝트 초기 환경 구축
- **Type:** Task / **우선순위:** P0 / **의존성:** 없음

```
우선순위: P0

### 작업 내용
- [ ] `uv`로 `pyproject.toml` 생성 (Python 3.13 고정, 런타임: fastapi, uvicorn, sqlalchemy, alembic, pydantic, pydantic-settings / 개발: pytest, httpx)
- [ ] `app/__init__.py`, `app/main.py`(FastAPI 앱 생성만) 추가
- [ ] `app/config.py` 추가 (pydantic-settings, `.env` 로드, `SEOUL_OPEN_API_KEY` 필드는 빈 값 허용)
- [ ] `app/models/`, `app/schemas/`, `app/routers/`, `app/services/`, `app/clients/` 패키지 생성 (`__init__.py`)
- [ ] `.env.example` 추가 (`SEOUL_OPEN_API_KEY=` 빈 값만)
- [ ] `.gitignore`에 `.env`, `*.db` 추가 및 `data/.gitkeep` 추가
- [ ] `GET /health` 엔드포인트 추가
- [ ] 공통 오류 응답 형식 핸들러 추가 (`{"error": {"code": "...", "message": "..."}}`, 형식은 순서 2에서 확정)
- [ ] 설정 로그에 API 키 원문이 출력되지 않음을 확인
- [ ] `README.md`에 실행법 추가 (`uv sync`, `uv run uvicorn app.main:app --reload`, `uv run pytest`)
- [ ] `/health` pytest 1건 추가

### 완료 기준
- [ ] `uv sync` 성공 (로컬 실행 결과 기록)
- [ ] `uv run pytest` 통과 (결과 기록)
- [ ] `uv run uvicorn app.main:app` 실행 후 `/health` 200 응답 확인
- [ ] 커밋 대상에 `.env`와 `*.db`가 없음을 `git status`로 확인

### 의존성
- 없음
```

---

## 순서 2. FE/AI API 계약 초안 작성 및 합의
- **Type:** Task / **우선순위:** P0 / **의존성:** 순서 1

```
우선순위: P0

### 작업 내용
- [ ] 엔드포인트 7개(HANDOFF 10번)별 요청 파라미터 표 작성
- [ ] 엔드포인트별 성공 응답 JSON 예시 작성
- [ ] 엔드포인트별 실패 응답 예시 작성 (공통 오류 형식 사용)
- [ ] 엘리베이터 상태 enum 확정: `NORMAL`, `OUT_OF_SERVICE`, `MAINTENANCE`, `UNKNOWN`
- [ ] 역 상태 enum 확정: `AVAILABLE`, `PARTIAL`, `UNAVAILABLE`, `UNKNOWN`
- [ ] `NORMAL`과 `UNKNOWN`만 섞인 역의 판정 규칙을 "미정"으로 표시하고 결정 대기 목록에 기록
- [ ] `estimated`(추정값 여부)와 `limitations`(한계 문구 목록) 필드 규칙 명문화
- [ ] 시각 규칙 명문화: 저장은 UTC, 응답은 KST ISO8601(offset 포함)
- [ ] 공통 오류 응답 형식 확정 (순서 1 구현과 일치)
- [ ] `docs/api-contract.md` 작성
- [ ] FE·AI 담당자에게 리뷰 요청, 피드백과 결정 사항을 문서에 반영

### 완료 기준
- [ ] 7개 엔드포인트가 모두 문서에 있다
- [ ] FE·AI 확인 기록이 있다 (미합의 항목은 "미정"으로 남긴다)
- [ ] `estimated`, `limitations` 규칙이 문서에 있다

### 의존성
- 순서 1
```

---

## 순서 3. 공공데이터 API 응답 구조 및 매핑 검증
- **Type:** Task / **우선순위:** P0 / **의존성:** 순서 1
- **참고:** 기존 Issue #1(조사)과 일부 겹친다. 기존 #1은 수정하지 않고, 이 Issue에서 실제 호출로 검증한다.

```
우선순위: P0

### 작업 내용
- [ ] 로컬 `.env`에 `SEOUL_OPEN_API_KEY` 설정 (채팅·커밋에 올리지 않는다)
- [ ] OA-15994 승강기 가동현황 실제 호출 (json), 응답 샘플을 저장하되 URL의 키는 제거하거나 마스킹
- [ ] OA-15994 확인: 전체 목록 여부, 정상·고장·점검 구분, 역명·호선, 위치 설명, 고유 ID, 갱신 시각, 1~8호선 범위, 정상 설비 포함 여부
- [ ] OA-101 역코드별 열차 시간표 실제 호출, 필드 의미 확인 (STATION_CD, LINE_NUM, TRAIN_NO, ARRIVETIME, LEFTTIME, WEEK_TAG, INOUT_TAG, DESTSTATION)
- [ ] OA-22724 최단경로 이동정보 호출 가능 여부, 환승·시간 정보 확인
- [ ] `station_edges` 간선 출처 결정 근거 정리 (OA-101 연속 역 추출 vs OA-22724)
- [ ] 호출 제한·응답 시간 측정 결과 기록
- [ ] OA-15994 역명 ↔ OA-101 STATION_CD 매핑 가능성 확인, 불일치 목록 작성
- [ ] 보완 API(OA-22485, OA-22725, OA-21212) 필요 여부 결론
- [ ] 샘플 파일 보관 경로 결정 (키 제거 확인 후 커밋)
- [ ] `docs/api-notes.md` 작성 (확인됨 / 미확인 / 미검증 구분)

### 완료 기준
- [ ] 데이터셋별 확인 결과 표가 `docs/api-notes.md`에 있다
- [ ] 샘플 파일에 API 키가 없음을 `grep`으로 확인
- [ ] 보완 API 필요 여부 결론이 있다
- [ ] 확인하지 못한 항목은 `(미검증)`으로 표시

### 의존성
- 순서 1
```

---

## 순서 4. SQLite·SQLAlchemy·Alembic 구성
- **Type:** Task / **우선순위:** P0 / **의존성:** 순서 1
- **참고:** 기존 Issue #4(설계)와 다르다. 이 Issue는 구현이다.

```
우선순위: P0

### 작업 내용
- [ ] `app/database.py`에 엔진, 세션, Base 추가 (기본 URL `sqlite:///data/dev.db`)
- [ ] `DATABASE_URL` 환경변수로 DB 경로 재정의 가능하게 설정
- [ ] 커넥션마다 `PRAGMA foreign_keys=ON` 실행하는 이벤트 리스너 추가
- [ ] 테스트용 DB를 메모리 또는 임시 파일로 분리하는 pytest fixture 추가
- [ ] Alembic 초기화, `render_as_batch=True` 설정, `env.py`에 metadata 연결
- [ ] 빈 초기 마이그레이션 생성, upgrade/downgrade 동작 확인
- [ ] UTC 저장, KST 응답 변환 유틸 추가 및 테스트
- [ ] 테스트 중 `data/dev.db`를 건드리지 않음을 확인

### 완료 기준
- [ ] `uv run alembic upgrade head` 성공 (결과 기록)
- [ ] FK 위반 시 실패하는 테스트 1건 통과
- [ ] `uv run pytest` 통과 (결과 기록)

### 의존성
- 순서 1
```

---

## 순서 5. 역·노선 모델 및 초기 데이터 적재
- **Type:** Feature / **우선순위:** P0 / **의존성:** 순서 3, 4

```
우선순위: P0

### 작업 내용
- [ ] `lines` 모델 추가 (`line_id` "1"~"8", 노선명, 운영기관) + 마이그레이션
- [ ] `stations` 모델 추가 (station_id, station_name, latitude, longitude) + 마이그레이션
- [ ] `station_lines` 모델 추가 (station_line_id, station_id FK, line_id FK, operator), 환승역은 행을 여러 개로 처리
- [ ] `station_external_ids` 모델 추가 (source, external_station_code, station_line_id), `UNIQUE(source, external_station_code)`, `station_line_id` 인덱스
- [ ] 1~8호선 시드 데이터 파일 추가 (출처는 순서 3 결과를 기재, 미검증 항목 표시)
- [ ] 멱등 적재 CLI 추가 (재실행 시 중복 행 없음)
- [ ] 적재 결과 검증 테스트 추가 (역 수, 노선 수, 환승역 2행 이상, 외부 코드 유일성)
- [ ] 외부 코드 1:1 대응 여부를 확인 결과에 기록 (미검증이면 표시)

### 완료 기준
- [ ] 적재 명령을 2번 실행해도 행 수가 같다 (결과 기록)
- [ ] 관련 pytest 통과
- [ ] 1~8호선 노선 수와 환승역 목록 확인 결과 기록

### 의존성
- 순서 3, 4
```

---

## 순서 6. 역 검색 및 상세 조회 API 구현
- **Type:** Feature / **우선순위:** P0 / **의존성:** 순서 2, 5

```
우선순위: P0

### 작업 내용
- [ ] Pydantic v2 스키마 추가 (StationSummary, StationDetail)
- [ ] `services/stations.py`에 검색 함수 추가 (검색어 `q`, 노선 필터는 선택)
- [ ] `GET /api/stations?q=` 추가, 입력 검증 (길이 제한, 빈 검색어 처리)
- [ ] `GET /api/stations/{station_id}` 추가, 없으면 공통 404 응답
- [ ] 환승역 응답에 노선 목록 포함
- [ ] pytest: 검색 성공, 결과 없음, 404, 잘못된 입력 422
- [ ] `/docs`(OpenAPI)에서 응답 스키마 확인

### 완료 기준
- [ ] 응답 필드가 순서 2 계약과 일치
- [ ] `uv run pytest` 통과 (결과 기록)

### 의존성
- 순서 2, 5
```

---

## 순서 7. 엘리베이터 데이터 수집 및 저장
- **Type:** Feature / **우선순위:** P0 / **의존성:** 순서 3, 5

```
우선순위: P0

### 작업 내용
- [ ] `clients/seoul_open_api.py` 추가 (httpx, 타임아웃 설정)
- [ ] URL에 키를 넣을 때만 키를 사용하고, 로그에는 키를 마스킹하는 함수 추가 및 테스트
- [ ] OA-15994 응답 파싱 함수 추가 (순서 3 샘플을 픽스처로 사용)
- [ ] `elevators` 모델 추가 (`UNIQUE(source, external_elevator_id)`, station_id, location_description, from_floor, to_floor, latitude/longitude nullable) + 마이그레이션
- [ ] `elevator_status` 모델 추가 (elevator_id, status, observed_at, fetched_at, source) + 마이그레이션
- [ ] 역 매칭: 매칭되지 않은 설비는 `elevators`에 넣지 않고 수집 리포트에 출력
- [ ] 수집 CLI 추가 (예: `uv run python -m app.cli collect-elevators`)
- [ ] 외부 API 실패 시 기존 상태 행 유지, `fetched_at` 미갱신 테스트
- [ ] 응답에 없는 설비를 정상 상태로 만들지 않는 테스트 추가

### 완료 기준
- [ ] 픽스처 기반 테스트 통과 (결과 기록)
- [ ] 실제 API 1회 수집 결과 건수와 매칭 실패 건수 기록 (미검증이면 표시)
- [ ] 로그에 API 키가 나오지 않음을 확인

### 의존성
- 순서 3, 5
```

---

## 순서 8. 역별 엘리베이터 상태 판정 로직 구현
- **Type:** Feature / **우선순위:** P0 / **의존성:** 순서 4

```
우선순위: P0

### 작업 내용
- [ ] 순수 함수 추가: 원본 상태·observed_at·현재 시각·TTL → 엘리베이터 상태 (판정은 조회 시점에 수행)
- [ ] TTL 초과 시 `UNKNOWN`, 상태 행이 없으면 `UNKNOWN`
- [ ] 역 상태 함수 추가: 전부 NORMAL → AVAILABLE, NORMAL 없고 비정상 있음 → UNAVAILABLE, 섞임 → PARTIAL, 정보 없음 → UNKNOWN
- [ ] NORMAL + UNKNOWN 혼합 규칙은 **사용자 결정 대기**로 표시하고 TODO로 남긴다
- [ ] TTL 상수를 설정값으로 분리 (기본 30분, 실제 갱신 주기 확인 후 확정 필요 `(미검증)`)
- [ ] 단위 테스트: TTL 경계값, 빈 목록, 전부 고장, 전부 정상, 혼합
- [ ] 판정 결과를 DB에 저장하지 않는지 코드에서 확인

### 완료 기준
- [ ] 단위 테스트 통과 (결과 기록)
- [ ] 판정 함수에 DB·네트워크 접근이 없음

### 의존성
- 순서 4
```

---

## 순서 9. 엘리베이터 목록 및 운행 상태 조회 API 구현
- **Type:** Feature / **우선순위:** P0 / **의존성:** 순서 2, 7, 8

```
우선순위: P0

### 작업 내용
- [ ] `GET /api/stations/{station_id}/elevators` 추가
- [ ] `GET /api/elevators/status` 추가 (상태 필터 선택)
- [ ] 응답에 판정 상태, observed_at, fetched_at 포함
- [ ] 외부 데이터가 오래됐으면 `limitations`에 표시
- [ ] 역이 없으면 404, 잘못된 상태 값이면 422
- [ ] pytest: 픽스처 DB 기준 API 테스트
- [ ] 응답 필드가 순서 2 계약과 일치하는지 대조

### 완료 기준
- [ ] `uv run pytest` 통과 (결과 기록)
- [ ] 데이터가 없는 설비를 정상으로 응답하지 않음을 테스트로 확인

### 의존성
- 순서 2, 7, 8
```

---

## 순서 10. 역 인접 그래프(station_edges) 구축
- **Type:** Feature / **우선순위:** P1 / **의존성:** 순서 3, 5

```
우선순위: P1

### 작업 내용
- [ ] `station_edges` 컬럼 확정 (from/to station_line_id, edge_type ADJACENT·TRANSFER, travel_seconds nullable, source)
- [ ] 모델과 마이그레이션 추가
- [ ] 간선 적재 스크립트 추가 (출처는 순서 3 결정에 따른다)
- [ ] 같은 역의 다른 노선 사이 환승 간선 생성 (기본 환승 5분, 설정값)
- [ ] 양방향 중복 방지, 재실행 시 멱등
- [ ] 그래프 검증 테스트: 1~8호선 연결성, 끊긴 구간 목록, 환승 쌍
- [ ] 간선 출처가 미검증인 부분을 문서에 표시

### 완료 기준
- [ ] 1~8호선 연결 여부 리포트 출력 (결과 기록)
- [ ] 적재 2회 실행 시 행 수 불변

### 의존성
- 순서 3, 5
```

---

## 순서 11. 엘리베이터 고장 시 대체역 추천 API 구현
- **Type:** Feature / **우선순위:** P1 / **의존성:** 순서 9, 10

```
우선순위: P1

### 작업 내용
- [ ] BFS로 인접역 탐색 (최대 탐색 깊이는 설정값, 기본값은 확정 필요)
- [ ] 후보 정렬 기준: 정상 엘리베이터 보유 우선, 동일 노선 우선
- [ ] `GET /api/stations/{station_id}/alternatives` 추가
- [ ] 응답에 후보 목록, 역 간 거리(간선 수), 후보 상태 포함
- [ ] 응답에 `limitations` 항상 포함 (실제 접근 가능성 미검증 문구)
- [ ] 대상 역이 정상이면 빈 후보 목록과 사유 반환
- [ ] 단위 테스트: 그래프 픽스처, 끊긴 그래프, 후보 없음

### 완료 기준
- [ ] `uv run pytest` 통과 (결과 기록)
- [ ] `limitations`가 모든 응답에 포함됨을 테스트로 확인

### 의존성
- 순서 9, 10
```

---

## 순서 12. 정규 열차 시간표 수집 및 조회 구현
- **Type:** Feature / **우선순위:** P1 / **의존성:** 순서 3, 5

```
우선순위: P1

### 작업 내용
- [ ] OA-101 클라이언트 추가 (순서 3 샘플 기준, 키는 URL에서만 사용)
- [ ] `train_schedules` 모델 추가 (station_line_id, service_day_type, direction, destination, train_no, arrival_time, departure_time 정수 초, source, effective_date) + 마이그레이션
- [ ] 인덱스 추가: `(station_line_id, service_day_type, direction, departure_time)`
- [ ] 24시 넘는 시각 변환 함수 추가 (예: 24:30 → 서비스일 기준 88200초), 테스트 포함
- [ ] WEEK_TAG 값을 WEEKDAY/SATURDAY/HOLIDAY로 매핑 (값의 의미는 순서 3에서 확인, 미검증이면 표시)
- [ ] 수집 CLI 추가 (멱등 적재)
- [ ] 조회 함수 추가: 역·방향·요일 기준, 도착 시각 이전 최근 열차

### 완료 기준
- [ ] 샘플 기준 테스트 통과 (결과 기록)
- [ ] 실제 적재 행 수와 적재 시간 기록

### 의존성
- 순서 3, 5
```

---

## 순서 13. 지하철 경로 탐색 API 구현
- **Type:** Feature / **우선순위:** P1 / **의존성:** 순서 2, 9, 10

```
우선순위: P1

### 작업 내용
- [ ] 외부 API(OA-22724) 사용 vs 자체 다익스트라 결정, 근거를 `docs/api-notes.md`에 기록
- [ ] 다익스트라 구현 (프로토타입 고정 가중치: 역 간 2분, 환승 5분. 시연용 추정값임을 응답에 표시)
- [ ] `GET /api/routes` 추가, 출발역·도착역 입력 검증
- [ ] 응답에 경로 역 목록, 환승 횟수, 총 소요 시간(`estimated: true`), 타임라인 포함
- [ ] 경로 구간 역의 엘리베이터 경고 목록 포함 (순서 9 상태 사용)
- [ ] 경로 없음 응답 규칙을 순서 2 계약과 맞춤
- [ ] pytest: 직선, 환승, 경로 없음, 엘리베이터 경고

### 완료 기준
- [ ] `uv run pytest` 통과 (결과 기록)
- [ ] 추정값이 `estimated`로 표시됨을 테스트로 확인

### 의존성
- 순서 2, 9, 10
```

---

## 순서 14. 출발 시각 역산 API 구현
- **Type:** Feature / **우선순위:** P1 / **의존성:** 순서 12, 13

```
우선순위: P1

### 작업 내용
- [ ] `POST /api/departure-time` 추가 (입력: 출발역, 도착역, 도착 희망 시각, 도보 시간, 엘리베이터 이용 시간, 여유 시간)
- [ ] 입력 검증 추가
- [ ] 기본값을 설정 파일로 분리 (집→역 도보 7분, 역 진입 3분, 도착역 출구 이동 3분, 시연용 추정값)
- [ ] 역산 로직: 도착 희망 시각에서 출구·이동·진입 시간을 빼고 시간표에서 탑승 가능한 열차 선택
- [ ] 시간표가 없으면 `estimated: true`와 `limitations` 표시
- [ ] 응답에 추천 출발 시각, 탑승·도착 예정 시각, 타임라인, 엘리베이터 경고 포함
- [ ] pytest: 심야 시각, 시간표 없음, 입력 오류
- [ ] 계산 결과를 DB에 저장하지 않음을 확인

### 완료 기준
- [ ] `uv run pytest` 통과 (결과 기록)
- [ ] 응답이 순서 2 계약과 일치

### 의존성
- 순서 12, 13
```

---

## 순서 15. 실시간 열차 도착정보 연동
- **Type:** Feature / **우선순위:** P2 / **의존성:** 순서 3, 5

```
우선순위: P2

### 작업 내용
- [ ] OA-12764 실제 호출, 필드 확인 (미검증)
- [ ] 클라이언트 추가 (타임아웃, 실패 처리)
- [ ] 메모리 TTL 캐시 추가 (약 30초, 테이블 생성 금지)
- [ ] 캐시 만료 테스트
- [ ] 외부 실패 시 빈 값과 "미확인" 표시
- [ ] 출발 시각 API에 실시간 정보를 포함할지 사용자와 결정

### 완료 기준
- [ ] 테스트 통과 (결과 기록)
- [ ] 실시간 정보가 없어도 시간표 기준 응답이 유지됨을 확인

### 의존성
- 순서 3, 5
```

---

## 순서 16. FE/AI 연동 점검 및 통합 테스트
- **Type:** Task / **우선순위:** P1 / **의존성:** 순서 9, 11, 13, 14

```
우선순위: P1

### 작업 내용
- [ ] AI용 5개 기능 시나리오 정의 (엘리베이터 상태, 대체역, 출발 시각, 고장·점검 목록, 경로 조회)
- [ ] 시나리오별 통합 테스트 추가
- [ ] FE 호출 확인 및 CORS 설정 필요 여부 확인·반영
- [ ] AI 측에서 샘플 JSON을 실제로 읽을 수 있는지 확인
- [ ] `/docs`(OpenAPI) 최종 점검
- [ ] README 정리 (실행법, 환경변수, 데이터 적재 순서, 미검증 항목 목록)
- [ ] 미검증 항목 최종 목록 작성

### 완료 기준
- [ ] 전체 `uv run pytest` 통과 (결과 기록)
- [ ] FE·AI 확인 기록이 있다
- [ ] 미검증 항목 목록이 README 또는 문서에 있다

### 의존성
- 순서 9, 11, 13, 14
```

---

## 2. 기존 Issue 수정안 (#3, #5)
사용자 요청에 따른 "라벨 → Issue Type" 수정안이다. **`gh issue edit`는 승인 후에만 실행한다.** 두 Issue의 본문에 있는 항목만 바꾼다. 번호와 담당자, 상태는 바꾸지 않는다.

### #3 GitHub Issues–Notion Tasks DB 자동 연동
| 변경 전 | 변경 후 |
|---|---|
| - GitHub Issue 생성 시 Notion Tasks DB에 업무 자동 등록 | (유지) |
| - 이슈 제목·설명·**라벨** 자동 동기화 | - 이슈 제목·설명·**Issue Type** 자동 동기화 (Labels는 사용하지 않음) |
| - FE / BE / AI 분야별 업무 자동 분류 | (유지) |
| - 중복 업무 등록 방지 | (유지, Issue URL 기준 명시) |

### #5 GitHub 버그 자동 분류 및 추적 환경 구축
| 변경 전 | 변경 후 |
|---|---|
| - GitHub Issue **라벨** 기반 업무 유형 자동 분류 설정 | - GitHub **Issue Type** 기반 업무 유형 자동 분류 설정 (Labels 미사용) |
| - Bug / Feature / Docs / Task 유형 구성 | - Bug / Feature / Docs / Task Issue Type 사용 (Task 지원은 확인, 나머지 `(미검증)`) |
| - Notion Bug Tracker 연결된 보기 구축 | - Notion Tasks DB의 **업무 유형 = Bug 필터 보기** 구축 (별도 DB 아님) |
| - 버그 자동 동기화 테스트 예정 | (유지, 미검증) |

---

## 3. 승인 전 확인할 결정 사항
1. 순서 0을 맨 앞에 둘지 확인. 표에서는 순서 0이다.
2. 순서 8: NORMAL + UNKNOWN 혼합 역의 상태(PARTIAL 또는 UNKNOWN).
3. 순서 8: 엘리베이터 상태 TTL 기본값(초안 30분, 갱신 주기 확인 후 확정).
4. 순서 13: 경로 탐색에 외부 API를 쓸지 자체 다익스트라를 쓸지.
5. 순서 15: 실시간 정보를 출발 시각 API에 포함할지.
