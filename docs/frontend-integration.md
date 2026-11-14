# FE/AI 연동 안내

> 상태: 초안. 상세 필드와 규칙은 [api-contract.md](api-contract.md)가 기준이다. 이 문서는 연동에 필요한 요약이다.
> 실행 방법은 README의 "FE/AI 로컬 연동 빠른 시작"을 본다. Base URL(로컬): `http://127.0.0.1:8000`

## 공통 규칙

- 시각: 응답은 KST ISO 8601(`+09:00`). 요청의 시각은 오프셋이 있어야 한다.
- 모든 업무 응답에 `limitations`(사용자에게 그대로 보여 줄 한국어 문장 배열)가 있다. **비어 있지 않으면 화면에 표시한다.**
- 오류 형식: `{"error": {"code": "NOT_FOUND|VALIDATION_ERROR|METHOD_NOT_ALLOWED|INTERNAL_ERROR", "message": "..."}}`. 없는 리소스 404, 입력 오류 422.
- CORS: 서버의 `CORS_ALLOWED_ORIGINS`에 FE Origin이 있어야 한다. 기본은 비어 있어 브라우저 호출이 막힌다. 허용 메서드는 GET, POST, OPTIONS, 헤더는 Content-Type, 쿠키·인증은 쓰지 않는다.
- 상태 값이 `null`이면 "정상"으로 표시하지 않는다. 확인하지 못했다는 뜻이다.

## API 목록

| Method | Path | 용도 | 비고 |
|---|---|---|---|
| GET | `/health` | 서버 확인 | `{"status":"ok"}` |
| GET | `/api/stations?q=&line=` | 역 검색 | `q` 1~20자 필수, `line` 1~8 선택 |
| GET | `/api/stations/{station_id}` | 역 상세 | |
| GET | `/api/stations/{station_id}/elevators` | 역별 엘리베이터와 역 상태 | `station_status.status`: AVAILABLE/PARTIAL/UNAVAILABLE/null |
| GET | `/api/elevators/status?status=` | 전체 설비 상태 | `status`: `가동중` 또는 `가동 안됨` |
| GET | `/api/stations/{station_id}/alternatives` | 대체역 후보 | 연결 데이터 기준, 접근 가능 여부 미검증 |
| POST | `/api/departure-time` | 권장 출발 시각 계산 | 시간표 미사용 추정 |
| GET | `/api/stations/{station_id}/arrivals` | 실시간 도착 | 외부 출처 미연동, 항상 UNCONFIRMED |

## 요청·응답 예시 (축약)

역 검색 `GET /api/stations?q=서울역`
```json
{"stations":[{"station_id":1,"station_name":"서울역","lines":[{"line_id":"1","line_name":"1호선"}]}],"count":1,"limitations":[]}
```

역별 엘리베이터 `GET /api/stations/1/elevators` (확인된 설비가 없는 경우)
```json
{"station":{"station_id":1,"station_name":"서울역"},
 "station_status":{"status":null,"data_fresh":false,"total_elevator_count":6,"unknown_elevator_count":6},
 "limitations":["확인 가능한 엘리베이터 상태가 없어 역 상태를 판정할 수 없음"],
 "elevators":[{"elevator_id":1,"status":null,"data_fresh":false,"unknown_reason":"STALE","observed_at":null,"fetched_at":"2026-11-13T09:00:00+09:00","...":"..."}]}
```

출발 시각 `POST /api/departure-time`
```json
// 요청
{"arrival_at":"2099-01-01T09:00:00+09:00","travel_minutes":30,"buffer_minutes":0}
// 응답 (일부)
{"recommended_departure_at":"2099-01-01T08:17:00+09:00","estimated_boarding_at":"2099-01-01T08:27:00+09:00",
 "estimated_alighting_at":"2099-01-01T08:57:00+09:00","arrival_at":"2099-01-01T09:00:00+09:00",
 "total_minutes":43,"estimated":true,"timetable_used":false,
 "elevator_warning":{"status":"UNCONFIRMED","items":[],"message":"..."},"limitations":["..."]}
```

실시간 도착 `GET /api/stations/1/arrivals`
```json
{"station":{"station_id":1,"station_name":"서울역"},"arrival_status":"UNCONFIRMED",
 "unconfirmed_reason":"PROVIDER_NOT_CONFIGURED","arrivals":[],"count":0,"fetched_at":null,
 "cache_hit":false,"cache_ttl_seconds":30,"limitations":["실시간 도착 정보 출처가 연동되지 않아 도착 시각을 확인하지 못함"]}
```

## 데이터 한계

- **엘리베이터 상태**: OA-15994 1회 수집 샘플(2026-11-13)만 있다. 원본에 관측 시각이 없어 `observed_at`은 항상 `null`이다. 기본 TTL 120분(임시)을 넘으면 정보 없음이다. 데모 DB에서는 샘플이 전부 정보 없음으로 보이는 것이 정상이다.
- **합성 상태**: 데모 DB의 `[DEMO/SYNTHETIC]` 설비 5건은 실제 상태가 아니다.
- **역·노선**: 240개 역, 1~8호선 전체가 아니다. 1호선은 일부 구간(10개 역)뿐이며, 해당 응답에는 `limitations`가 붙는다. 9호선은 없다.
- **환승**: 공식 근거가 있는 38건만 있다. 인접역 간선 239건은 공식 근거가 없는 **잠정(PROVISIONAL)** 데이터이고 데모 DB에만 있다. 운영용 적재 경로는 이를 막는다. 대체역 결과는 이 간선에 의존하며 실제 이동 가능성은 검증되지 않았다.
- **출발 시각**: 시간표·경로·실시간 정보를 쓰지 않는다. 이동 시간은 호출자가 넣은 값이고 도보·진입·출구 시간(기본 7/3/3분)은 검증되지 않은 추정이다. `buffer_minutes` 기본값 0은 FE/AI 검토 대상이다.
- **엘리베이터 경고**: 역·설비 연동이 없어 항상 `UNCONFIRMED`다.
- **실시간 도착**: 외부 출처(OA-12764)의 호출 방식이 검증되지 않아 연동하지 않았다.

## 아직 구현되지 않은 기능

- 경로 탐색 `GET /api/routes`(#37, 다른 팀원 담당)
- AI 최적 경로·빠른 대체역 추천
- 열차 시간표 수집과 시간표 기반 열차 선택(#35 이후)
- 실시간 도착 외부 연동(OA-12764)
- 출발 시각 API의 엘리베이터 경고 연동, 실시간 정보 반영 여부 결정(#41)
- 실제 `data/dev.db` 적재, 정기 수집 스케줄
- 배포 환경(호스팅, HTTPS, SQLite 영속성·백업)과 계약 문서의 FE/AI 리뷰 확정(#14, #45)
