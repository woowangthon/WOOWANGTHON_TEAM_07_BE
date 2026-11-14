# 시연 체크리스트와 FE/AI 인계

> 로컬 시연 기준이다. 배포는 아직 없다([deployment-considerations.md](deployment-considerations.md)). API 상세는 [frontend-integration.md](frontend-integration.md), 계약은 [api-contract.md](api-contract.md).

## 시연 전 체크리스트 (순서대로)

- [ ] `uv sync` 완료, `.env` 준비(`cp .env.example .env`). **FE 개발 서버 주소를 `CORS_ALLOWED_ORIGINS`에 넣었다.**
- [ ] 데모 DB 생성: `uv run python -m app.cli.build_demo_db --include-provisional-adjacent` → 출력된 `데모 DB:` 경로 확인
- [ ] 서버 실행: `DATABASE_URL=sqlite:///<절대경로>/demo.db uv run uvicorn app.main:app --port 8000`
- [ ] 점검 스크립트: `uv run python scripts/demo_check.py` → `FAIL=0`, 종료 코드 0
- [ ] FE 화면에서 브라우저 콘솔에 CORS 오류가 없다(있으면 Origin 오탈자·포트 확인)
- [ ] Swagger UI(`/docs`)가 열린다
- [ ] `data/dev.db`를 쓰지 않는 서버인지 확인(실행 로그의 DB 경로가 아닌 `DATABASE_URL` 값 기준)

점검 스크립트의 WARN(정보 없음, 후보 없음, 도착 UNCONFIRMED)은 현재 데이터 상태에서 **정상**이다.

## 시연에서 보이는 것과 말해야 할 것

| 화면 | 현재 동작 | 반드시 함께 안내 |
|---|---|---|
| 엘리베이터 상태 | 샘플은 과거 응답이라 대부분 "정보 없음" | 실시간 상태가 아님. 합성 5건은 `[DEMO/SYNTHETIC]`이며 실제 아님 |
| 대체역 | 인접 간선이 잠정(PROVISIONAL)이고 후보 설비가 대부분 미확인이라 후보가 적거나 없음 | 접근 가능 여부는 검증되지 않음 |
| 출발 시각 | 입력한 이동 시간으로 역산한 추정값 | 시간표·실시간 미사용, 도보 등 기본값은 추정 |
| 도착 정보 | 항상 UNCONFIRMED | 외부 출처 미연동 |
| 1호선 | 일부 구간 10개 역만 | `limitations` 문구를 그대로 표시 |

화면 규칙: `limitations`는 비어 있지 않으면 표시, `status: null`은 정상으로 표시하지 않는다, 도착·출발 추정값을 확정 시각처럼 표현하지 않는다.

## FE 인계

- Base URL: `http://127.0.0.1:8000`, 모든 시각은 KST(`+09:00`), 오류는 `{"error":{"code","message"}}`.
- 역 ID는 `GET /api/stations?q=`로 얻는다(데모 DB 기준 서울역 = 1이지만 ID에 의존하지 않는다).
- 한글 쿼리는 URL 인코딩한다(`fetch`는 `URLSearchParams` 사용).
- 출발 시각은 `POST` + `Content-Type: application/json`, `arrival_at`은 오프셋 포함 ISO 8601.
- 막히면: 서버 로그 → `scripts/demo_check.py` → `/docs` 순서로 확인.

## AI 인계

- 사용할 수 있는 입력: 역·설비 상태(`/api/stations/{id}/elevators`), 대체역 후보, 출발 시각 계산(경로·시간표 미사용).
- 아직 없는 것: 경로 탐색(#37), 시간표, 실시간 도착. 이 값들을 추정해서 채우지 않는다. 응답의 `estimated`, `limitations`, `status: null`을 판단에 그대로 반영한다.
- 계약 리뷰 항목(`api-contract.md` 10.2절)과 `buffer_minutes` 기본값 0의 적정성은 FE/AI 검토가 필요하다.

## 이 문서 범위 밖(보류)

경로 탐색 #37, AI 최적화, 시간표 수집, OA-12764 실제 연동, 실제 DB 적재, 실제 배포.
