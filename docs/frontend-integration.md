# FE/AI 연동 안내

> 상태: 초안. 상세 필드와 규칙은 [api-contract.md](api-contract.md)가 기준이다. 이 문서는 연동에 필요한 요약이다.
> 시연 체크리스트는 [demo-handoff.md](demo-handoff.md), 배포 조건은 [deployment-considerations.md](deployment-considerations.md), 실행 방법은 README의 "FE/AI 로컬 연동 빠른 시작"을 본다. Base URL(로컬): `http://127.0.0.1:8000`

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
| GET | `/api/stations?q=&line=` | 역 검색 | `q` 1~20자 필수(전체 목록 불가, 미결정), `line` 1~8 선택. 역명 부분 일치, `station_name`에 괄호 부제가 있을 수 있음(예: `교대(법원, 검찰청)`) |
| GET | `/api/stations/{station_id}` | 역 상세 | |
| GET | `/api/stations/{station_id}/elevators` | 역별 엘리베이터와 역 상태 | `station_status.status`: AVAILABLE/PARTIAL/UNAVAILABLE/null |
| GET | `/api/elevators/status?status=&line=` | 전체 설비 상태 | `status`: `가동중` 또는 `가동 안됨`, `line`: 1~8. 각 설비에 `lines`(역이 지나는 전체 노선) |
| GET | `/api/stations/{station_id}/alternatives` | 대체역 후보 | 연결 데이터 기준, 접근 가능 여부 미검증 |
| GET | `/api/routes?from=&to=` | 두 역 사이 경로 | 소요 시간은 추정(역 간 2분·환승 5분). 경로 위 역의 `elevator_warning` 포함. 없는 경로·없는 역 404, 같은 역 422 |
| POST | `/api/departure-time` | 권장 출발 시각 계산 | 기본은 추정. 환승 없는 경로+시간표에 열차가 있으면 `selected_train`으로 열차 시각 사용. `buffer_minutes` 기본 5. `from_station_id`+`to_station_id`를 주면 경로 위 역의 엘리베이터 경고(하나만 주면 422, 없는 역 404, 같은 역 422, 경로 없음은 오류 아닌 `UNCONFIRMED`) |
| GET | `/api/stations/{station_id}/arrivals` | 실시간 도착 | 외부 출처 미연동, 항상 UNCONFIRMED |

## 요청·응답 예시

아래 JSON은 데모 DB로 서버를 띄워 실제로 받은 응답이다(2026-11-14, 로컬). 긴 배열은 일부만 남겼다. 값(시각, 개수)은 데모 DB 기준이다.

역 검색 `GET /api/stations?q=서울역`
```json
{"stations":[{"station_id":1,"station_name":"서울역","lines":[{"line_id":"1","line_name":"1호선"},{"line_id":"4","line_name":"4호선"}]}],"count":1,"limitations":["1호선은 일부 구간의 역만 포함되어 있어 전체 구간을 포함하지 않음"]}
```

역별 엘리베이터 `GET /api/stations/1/elevators` (설비 6개 중 1개만 표시, 전부 정보 없음)
```json
{"station":{"station_id":1,"station_name":"서울역"},"station_status":{"status":null,"data_fresh":false,"total_elevator_count":6,"unknown_elevator_count":6},"limitations":["확인 가능한 엘리베이터 상태가 없어 역 상태를 판정할 수 없음"],"elevators":[{"elevator_id":1,"elevator_name":"승강기)엘리베이터-서울(1)역 2번 출구측 외부#1","location_description":"2번 출입구","operation_section":"B1-1F","status":null,"data_fresh":false,"unknown_reason":"STALE","observed_at":null,"fetched_at":"2026-11-13T09:00:00+09:00"}]}
```

출발 시각 `POST /api/departure-time` 요청 (`Content-Type: application/json`)
```json
{"arrival_at":"2026-12-01T09:00:00+09:00","travel_minutes":30}
```
응답
```json
{"recommended_departure_at":"2026-12-01T08:12:00+09:00","estimated_boarding_at":"2026-12-01T08:27:00+09:00","estimated_alighting_at":"2026-12-01T08:57:00+09:00","arrival_at":"2026-12-01T09:00:00+09:00","total_minutes":48,"date_changed":false,"already_past":false,"estimated":true,"timetable_used":false,"inputs":{"travel_minutes":30,"buffer_minutes":5,"walk_to_station_minutes":7,"station_entry_minutes":3,"exit_walk_minutes":3},"defaults_applied":["buffer_minutes","walk_to_station_minutes","station_entry_minutes","exit_walk_minutes"],"timeline":[{"kind":"WALK_TO_STATION","label":"집에서 역까지 도보","start_at":"2026-12-01T08:12:00+09:00","end_at":"2026-12-01T08:19:00+09:00","minutes":7},{"kind":"RIDE","label":"열차 이동(외부 입력 예상 시간)","start_at":"2026-12-01T08:27:00+09:00","end_at":"2026-12-01T08:57:00+09:00","minutes":30}],"elevator_warning":{"status":"UNCONFIRMED","items":[],"message":"역·설비 상태 정보가 입력되지 않아 엘리베이터 경고를 확인하지 못함"},"limitations":["실제 열차 시간표를 사용하지 않은 추정 계산이며 탑승 가능한 열차는 확인되지 않음","역·설비 상태 정보가 입력되지 않아 엘리베이터 경고를 확인하지 못함"]}
```
`timeline`은 실제로는 5구간(도보, 역 진입, 여유 시간, 열차 이동, 출구 이동)이고 `limitations`는 4개다. 위는 일부만 남긴 것이다. 위 예시는 기본값(대기 5분 포함) 기준으로 합계를 맞춘 값이다(7+3+5+30+3 = 48분). 실제 서버 응답으로 재확인하지는 않았다(미검증).

경로 `GET /api/routes?from=3&to=7` 응답 필드(전체 예시는 [api-contract.md](api-contract.md) 8.4절)
```json
{"from_station":{},"to_station":{},"stations":[{"station_id":3,"station_name":"강남","lines":[],"is_transfer":false,"station_status":{}}],"transfer_count":1,"total_minutes":9,"estimated":true,"timeline":[{"kind":"RIDE","minutes":2},{"kind":"TRANSFER","minutes":5}],"elevator_warning":{"status":"WARNING","items":[{"station_id":3,"station_name":"강남","kind":"UNAVAILABLE","message":""}],"message":""},"limitations":[]}
```
`elevator_warning.status`는 `WARNING`(고장·일부 고장 역 있음), `UNCONFIRMED`(확인 못 한 역 있음), `CLEAR`(확인된 설비 기준 모두 가동중)다. `CLEAR`도 "이용 가능"이라고 단정하지 말고 "확인된 설비 기준"이라고 표시한다.

실시간 도착 `GET /api/stations/1/arrivals`
```json
{"station":{"station_id":1,"station_name":"서울역"},"arrival_status":"UNCONFIRMED","unconfirmed_reason":"PROVIDER_NOT_CONFIGURED","arrivals":[],"count":0,"fetched_at":null,"cache_hit":false,"cache_ttl_seconds":30,"limitations":["실시간 도착 정보 출처가 연동되지 않아 도착 시각을 확인하지 못함"]}
```

## 데이터 한계

- **엘리베이터 상태**: OA-15994 1회 수집 샘플(2026-11-13)만 있다. 원본에 관측 시각이 없어 `observed_at`은 항상 `null`이다. 기본 TTL 120분(임시)을 넘으면 정보 없음이다. 데모 DB에서는 샘플이 전부 정보 없음으로 보이는 것이 정상이다.
- **합성 상태**: 데모 DB의 `[DEMO/SYNTHETIC]` 설비 5건은 실제 상태가 아니다.
- **역·노선**: 240개 역, 1~8호선 전체가 아니다. 1호선은 일부 구간(10개 역)뿐이며, 해당 응답에는 `limitations`가 붙는다. 9호선은 없다.
- **환승**: 공식 근거가 있는 38건만 있다. 인접역 간선은 공식 자료가 없다. 데모 DB는 잠정(PROVISIONAL) 239건을, 시연 서버는 열차 시간표에서 추출한 간선(`source=OA-101`)을 쓴다. 경로·대체역 결과는 이 간선에 의존하며 실제 이동 가능성은 검증되지 않았다.
- **출발 시각**: 실시간 정보를 쓰지 않는다. `from_station_id`+`to_station_id`를 주고 경로가 환승 없는 한 노선이면 열차 시간표의 계획 시각으로 열차를 골라 맞추고(`selected_train`, `timetable_used: true`, 이때 `travel_minutes`는 계산에 쓰지 않는다), 그 밖에는 `selected_train: null`이고 이동 시간은 호출자가 넣은 값(경로 API의 `total_minutes` 등)이고 도보·진입·출구 시간(기본 7/3/3분)과 승강장 대기(기본 5분)는 검증되지 않은 추정이다.
- **엘리베이터 경고**: 출발 시각 API는 역 id를 주지 않으면 항상 `UNCONFIRMED`다. 역 id를 주면 경로 위 모든 역을 판정하지만, 설비 정보가 없는 역은 `UNCONFIRMED`로 남는다.
- **경로 소요 시간**: 역 간 2분·환승 5분을 가정한 값이다. 실제 열차 시간표나 환승 보행 시간이 아니다.
- **실시간 도착**: 외부 출처(OA-12764)의 호출 방식이 검증되지 않아 연동하지 않았다.

## 아직 구현되지 않은 기능

- AI 최적 경로·빠른 대체역 추천
- 환승 경로의 시간표 열차 선택(환승 없는 한 노선 경로만 시간표로 고른다), 공휴일 시간표
- 실시간 도착 외부 연동(OA-12764), 출발 시각 API의 실시간 정보 반영(#41, 넣지 않기로 함)
- 정기 수집 스케줄(수동 CLI)
- 클라우드 배포(시연은 집 데스크탑 터미널 실행)와 계약 문서의 FE/AI 리뷰 확정(#14)
