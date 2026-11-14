# Backend API 계약

> 상태: 결정 사항 반영 (2026-11-14, `main` 5d2b26a 기준 라우터·스키마와 대조). **FE/AI 리뷰 전이며 Issue #14는 Open 상태다.** 10.2절 항목을 검토한다.
> 기준: Issue #14 본문에 합의된 상태 판정 규칙. 데이터 근거는 `docs/api-notes.md`.
> 표기: `(D번호)`는 확정된 결정(10.1절), `(R번호)`는 FE/AI 검토 항목(10.2절), `(미검증)`은 실제 데이터로 확인하지 못한 항목이다.
> JSON 예시의 역 ID, 시각, 설비 상태는 **형식을 보여 주는 예시**다. 역 마스터는 1~8호선 전체가 아니라 부분 시드(240개 역, 1호선은 10개 역)이며 공식 전체 마스터는 아직 없다(#1, #19, 9절).

---

## 0. 공통 규칙

### 0.1 경로
- 모든 업무 API는 `/api` 아래에 둔다. 헬스 체크 `GET /health`는 예외다.
- 필드 이름은 `snake_case`를 쓴다.

### 0.2 시각
- **저장:** UTC ISO 8601, 초 단위, `Z` 접미사 (예: `2026-11-13T05:00:00Z`).
- **응답:** KST ISO 8601, `+09:00` 오프셋 (예: `2026-11-13T14:00:00+09:00`).
- 응답의 시각 필드는 모두 KST다. 변환은 `app/timeutil.py`의 `utc_str_to_kst_iso`를 쓴다.
- 시각 필드 이름은 `_at`으로 끝난다. 값이 없으면 `null`이다.

### 0.3 필수·선택·nullable 표기
- **필수:** 항상 있다. 값이 없어도 키는 있다.
- **선택:** 요청 파라미터에만 쓴다. 생략할 수 있다.
- **nullable:** 키는 항상 있고, 값이 `null`일 수 있다.
- 응답 객체는 키를 생략하지 않는다. 값이 없으면 `null`이나 빈 배열을 쓴다.

### 0.4 `limitations` 배열
- 업무 응답에는 `limitations`가 **항상** 있다. 내용이 없으면 빈 배열 `[]`이다.
- 각 문자열은 FE가 사용자에게 그대로 보여 줄 수 있는 한국어 문장이다.
- 불확실성을 알리는 기본 수단이다.

| 상황 | 표준 문구 |
|---|---|
| 일부 설비 상태 미확인 (`0 < C < T`) | 일부 설비 상태가 확인되지 않아 역 전체 상태를 단정할 수 없음 |
| 확인된 설비 없음 (`C = 0`, `T > 0`) | 확인 가능한 엘리베이터 상태가 없어 역 상태를 판정할 수 없음 |
| 엘리베이터 없음 (`T = 0`) | 엘리베이터 정보가 없어 역 상태를 판정할 수 없음 |
| 1호선 역이 응답에 포함됨 | 1호선은 일부 구간의 역만 포함되어 있어 전체 구간을 포함하지 않음 |
| 전체 설비 목록에 미확인 설비 존재 | 일부 설비의 상태 정보가 없거나 오래되어 현재 상태를 확인하지 못함 |
| 출발 시각: 시간표로 열차를 고름 (#38) | 열차 시간표의 계획 시각으로 탑승 열차를 골랐으며 지연과 실제 운행 여부는 반영되지 않음 |
| 〃 방향 추정 | 열차 방향은 시간표의 정차 순서로 추정한 것이며 공식 방향 정보로 검증되지 않음 |
| 〃 입력 이동 시간 미사용 | 열차 시간표로 이동 시간을 정해 입력한 예상 이동 시간은 계산에 쓰지 않음 |
| 〃 공휴일 정보 없음 | 공휴일 정보가 없어 요일만으로 평일·토요일·일요일 시간표를 골랐음 |
| 〃 맞는 열차 없음(폴백) | 시간표에서 도착 희망 시각에 맞는 열차를 찾지 못해 추정 계산을 사용함 |
| 〃 환승 경로(폴백) | 환승이 필요한 경로는 시간표로 열차를 고르지 않고 추정 계산을 사용함 |

1호선 문구(D11)는 `GET /api/stations`에서 `line=1`을 요청했거나 결과에 1호선 역이 있을 때, `GET /api/stations/{station_id}`에서 그 역이 1호선일 때 `limitations`에 포함한다. 1호선은 현재 일부 구간만 확보했다(9절).

표준 문구는 Backend 상수로 관리하고 FE는 받은 문구를 그대로 표시한다(D9). 문구를 바꾸면 이 표와 함께 바꾼다(R5).

### 0.5 `estimated`
- 추정값을 포함하는 응답에만 `estimated: boolean`이 있다. 경로·출발 시각 API(#37, #39)가 해당한다.
- 역·엘리베이터 상태 API에는 `estimated`가 없다. 상태는 추정이 아니라 저장된 값을 판정한 결과다.

### 0.6 오류 응답

모든 오류 응답은 다음 형식이다.

```json
{"error": {"code": "NOT_FOUND", "message": "요청한 리소스를 찾을 수 없습니다."}}
```

- `error`는 객체 하나만 가진다. 그 안에는 `code`와 `message`만 있다.
- 구현 위치: `app/errors.py`.

| HTTP 상태 | `error.code` | 발생 상황 |
|---|---|---|
| 404 | `NOT_FOUND` | 없는 역 ID, 없는 경로 |
| 405 | `METHOD_NOT_ALLOWED` | 허용되지 않은 HTTP 메서드 |
| 422 | `VALIDATION_ERROR` | 요청 파라미터·본문 형식 오류 |
| 500 | `INTERNAL_ERROR` | 처리되지 않은 서버 오류. 내부 정보는 응답에 없다 |

- 422 응답에는 필드별 상세를 넣지 않는다(R4 검토 대상).
- 현재 구현에는 `HTTP_ERROR`(위에 없는 HTTP 오류)도 있다. 업무 오류는 `AppError`로 코드를 지정한다.

### 0.7 빈 결과
- 검색 결과가 0건이면 **200**과 빈 배열이다. 404가 아니다(D5).
- 404는 리소스 ID가 없을 때만 쓴다.

---

## 1. 엔드포인트 목록

업무 엔드포인트 9개(Issue #8의 7개 + 경로 `GET /api/routes` + 역 단위 상태 목록 `GET /api/stations/status`)와 헬스 체크다.

| # | Method | Path | 이 문서 | 구현 Issue | 상태 |
|---|---|---|---|---|---|
| 0 | GET | `/health` | 확정 | #13 | 구현 완료 |
| 1 | GET | `/api/stations` | 2절 | #21 | 계약 초안 |
| 2 | GET | `/api/stations/{station_id}` | 3절 | #22 | 계약 초안 |
| 3 | GET | `/api/stations/{station_id}/elevators` | 4절 | #28 | 계약 초안 |
| 4 | GET | `/api/elevators/status` | 5절 | #28 | 계약 초안 |
| 5 | GET | `/api/stations/{station_id}/alternatives` | 8.1절 | #33 | 계약 초안(구현됨) |
| 6 | GET | `/api/routes` | 8.4절 | #36, #37 | 계약 초안(구현됨, 추정 소요 시간) |
| 7 | POST | `/api/departure-time` | 8.2절 | #39 | 계약 초안(구현됨, 시간표 미사용, 역 id를 주면 경로 경고) |
| 8 | GET | `/api/stations/{station_id}/arrivals` | 8.3절 | #40, #41 | 계약 초안(구현됨, 외부 출처 미연동) |
| 9 | GET | `/api/stations/status` | 5.5절 | — | 구현됨(D20). 고정 경로이며 `/api/stations/{station_id}`보다 먼저 등록 |

---

## 2. 역 목록·검색 — `GET /api/stations`

### 2.1 요청

| 파라미터 | 위치 | 필수 | 타입 | 규칙 |
|---|---|---|---|---|
| `q` | query | 선택 | string | 생략하면 전체 역 목록(`line` 필터는 적용). 주면 앞뒤 공백을 제거한 뒤 1~20자. 명시한 빈 값·공백만 있는 값(`q=`, `q=%20`)은 422 (D4, D19) |
| `line` | query | 선택 | string | `"1"`~`"8"` 중 하나. 그 외는 422 |

요청 예시: `GET /api/stations?q=서울&line=1`, `GET /api/stations`(전체), `GET /api/stations?line=2`(2호선 전체)

- 검색은 **저장된 역명 전체에 `q`가 포함되는지**(부분 일치, 대소문자 구분 없는 별도 정규화 없음)로 찾는다. 역명은 원본 표기 그대로라 괄호 부제가 붙은 역이 있다(예: `교대(법원, 검찰청)`, `잠실(송파구청)`). 부제를 뗀 이름(`교대`)으로 검색하면 찾히지만, 응답의 `station_name`은 괄호를 포함한다. 괄호를 뗀 비교는 호출하는 쪽(AI)이 한다.
- `q`를 생략하면 전체 역을 돌려준다(#73, D19). 정렬·환승역 1회·빈 결과 200·`limitations` 규칙은 검색과 같다. 페이징은 없다(D10). `station_id`는 백엔드 내부 정수 ID이며 연속이라는 보장이 없다. FE Mock ID나 외부 역 코드와 섞지 않는다.

### 2.2 응답 (200)

```json
{
  "stations": [
    {
      "station_id": 1,
      "station_name": "서울역",
      "lines": [
        {"line_id": "1", "line_name": "1호선"},
        {"line_id": "4", "line_name": "4호선"}
      ]
    }
  ],
  "count": 1,
  "limitations": []
}
```

### 2.3 필드

| 필드 | 타입 | 필수 | nullable | 설명 |
|---|---|---|---|---|
| `stations` | array | 필수 | 아니오 | `StationSummary` 목록. 0건이면 `[]` |
| `count` | integer | 필수 | 아니오 | `stations`의 길이 |
| `limitations` | array[string] | 필수 | 아니오 | 0.4절 |
| `StationSummary.station_id` | integer | 필수 | 아니오 | 내부 역 ID (`stations.station_id`) |
| `StationSummary.station_name` | string | 필수 | 아니오 | 역명 |
| `StationSummary.lines` | array | 필수 | 아니오 | 노선 목록. 환승역은 2개 이상. `line` 필터와 무관하게 전체 노선 |
| `lines[].line_id` | string | 필수 | 아니오 | `"1"`~`"8"` |
| `lines[].line_name` | string | 필수 | 아니오 | 예: `"1호선"` |

**정렬과 페이징 (D10):** `stations`는 역명 오름차순이고, 역명이 같으면 `station_id` 오름차순이다. MVP에서는 페이징을 적용하지 않는다.

---

## 3. 역 상세 — `GET /api/stations/{station_id}`

### 3.1 요청

| 파라미터 | 위치 | 필수 | 타입 | 규칙 |
|---|---|---|---|---|
| `station_id` | path | 필수 | integer | 정수가 아니면 422. 없는 ID면 404 |

### 3.2 응답 (200)

1호선이 포함된 역(`lines`에 `1` 포함)은 `limitations`에 D11 문구를 넣는다. 예시의 서울역이 그렇다. 좌표가 확인되지 않은 역은 `latitude`, `longitude`가 `null`이며, 임의로 채우지 않는다(D13).

```json
{
  "station_id": 1,
  "station_name": "서울역",
  "latitude": null,
  "longitude": null,
  "is_transfer": true,
  "lines": [
    {"line_id": "1", "line_name": "1호선"},
    {"line_id": "4", "line_name": "4호선"}
  ],
  "limitations": [
    "1호선은 일부 구간의 역만 포함되어 있어 전체 구간을 포함하지 않음"
  ]
}
```

### 3.3 필드

| 필드 | 타입 | 필수 | nullable | 설명 |
|---|---|---|---|---|
| `station_id` | integer | 필수 | 아니오 | 내부 역 ID |
| `station_name` | string | 필수 | 아니오 | 역명 |
| `latitude` | number | 필수 | **예** | 좌표가 없으면 `null` 허용(D8). 좌표 출처 미정(9절) |
| `longitude` | number | 필수 | **예** | 위와 같음 |
| `is_transfer` | boolean | 필수 | 아니오 | `lines`가 2개 이상이면 `true` |
| `lines` | array | 필수 | 아니오 | `lines[].line_id`, `lines[].line_name` |
| `limitations` | array[string] | 필수 | 아니오 | 0.4절 |

### 3.4 오류

| 상황 | 상태 | `error.code` |
|---|---|---|
| 없는 `station_id` | 404 | `NOT_FOUND` |
| `station_id`가 0 이하 (`0`, `-1` 등) | 404 | `NOT_FOUND` (D12) |
| `station_id`가 정수가 아님 | 422 | `VALIDATION_ERROR` |

---

## 4. 역별 엘리베이터 목록 — `GET /api/stations/{station_id}/elevators`

역 단위 상태(`station_status`)와 설비별 상태(`elevators`)를 함께 준다. 판정 규칙은 6절이다.

### 4.1 요청

| 파라미터 | 위치 | 필수 | 타입 | 규칙 |
|---|---|---|---|---|
| `station_id` | path | 필수 | integer | 3.1절과 같다 |

### 4.2 응답 (200) — 일부 설비만 확인된 예

```json
{
  "station": {"station_id": 1, "station_name": "서울역"},
  "station_status": {
    "status": "PARTIAL",
    "data_fresh": false,
    "total_elevator_count": 3,
    "unknown_elevator_count": 1
  },
  "limitations": [
    "일부 설비 상태가 확인되지 않아 역 전체 상태를 단정할 수 없음"
  ],
  "elevators": [
    {
      "elevator_id": 101,
      "elevator_name": "승강기)엘리베이터-서울(1)역 2번 출구측 외부#1",
      "location_description": "2번 출입구",
      "operation_section": "B1-1F",
      "status": "가동중",
      "data_fresh": true,
      "unknown_reason": null,
      "observed_at": null,
      "fetched_at": "2026-11-13T14:00:00+09:00"
    },
    {
      "elevator_id": 102,
      "elevator_name": "승강기)엘리베이터-서울(1)역 섬식(상) 5-1 내부#1",
      "location_description": "시청 방면5-1,남영 방면6-4",
      "operation_section": "B2-B1",
      "status": "가동 안됨",
      "data_fresh": true,
      "unknown_reason": null,
      "observed_at": null,
      "fetched_at": "2026-11-13T14:00:00+09:00"
    },
    {
      "elevator_id": 103,
      "elevator_name": "승강기)엘리베이터-서울(1)역 3번 출구측 외부#3",
      "location_description": "3번 출입구",
      "operation_section": "B1-1F",
      "status": null,
      "data_fresh": false,
      "unknown_reason": "STALE",
      "observed_at": null,
      "fetched_at": "2026-11-13T09:00:00+09:00"
    }
  ]
}
```

### 4.3 응답 (200) — 확인된 설비가 없는 예

```json
{
  "station": {"station_id": 2, "station_name": "시청"},
  "station_status": {
    "status": null,
    "data_fresh": false,
    "total_elevator_count": 2,
    "unknown_elevator_count": 2
  },
  "limitations": [
    "확인 가능한 엘리베이터 상태가 없어 역 상태를 판정할 수 없음"
  ],
  "elevators": [
    {
      "elevator_id": 201,
      "elevator_name": "승강기)엘리베이터-시청역 1번 출구측 외부#1",
      "location_description": "1번 출입구",
      "operation_section": "B1-1F",
      "status": null,
      "data_fresh": false,
      "unknown_reason": "NO_DATA",
      "observed_at": null,
      "fetched_at": null
    },
    {
      "elevator_id": 202,
      "elevator_name": "승강기)엘리베이터-시청역 2번 출구측 외부#2",
      "location_description": "2번 출입구",
      "operation_section": "B1-1F",
      "status": null,
      "data_fresh": false,
      "unknown_reason": "STALE",
      "observed_at": null,
      "fetched_at": "2026-11-12T09:00:00+09:00"
    }
  ]
}
```

### 4.4 필드

| 필드 | 타입 | 필수 | nullable | 설명 |
|---|---|---|---|---|
| `station` | object | 필수 | 아니오 | `station_id`, `station_name` |
| `station_status.status` | string | 필수 | **예** | `"AVAILABLE"` / `"PARTIAL"` / `"UNAVAILABLE"` / `null` (6.2절) |
| `station_status.data_fresh` | boolean | 필수 | 아니오 | 6.2절 |
| `station_status.total_elevator_count` | integer | 필수 | 아니오 | 역에 연결된 EV 설비 수 |
| `station_status.unknown_elevator_count` | integer | 필수 | 아니오 | 정보 없음 또는 오래된 설비 수 |
| `limitations` | array[string] | 필수 | 아니오 | 0.4절 |
| `elevators` | array | 필수 | 아니오 | 설비 목록. 설비가 없으면 `[]` |
| `elevators[].elevator_id` | integer | 필수 | 아니오 | 내부 설비 ID |
| `elevators[].elevator_name` | string | 필수 | 아니오 | 원본 설비명(`ELVTR_NM`) |
| `elevators[].location_description` | string | 필수 | **예** | 원본 설치 위치(`INSTL_PSTN`). 방면 원문 그대로 |
| `elevators[].operation_section` | string | 필수 | **예** | 운행 층 구간(`OPR_SEC`) |
| `elevators[].status` | string | 필수 | **예** | `"가동중"` / `"가동 안됨"` / `null`. `null`이면 FE는 "정보 없음"으로 표시한다 |
| `elevators[].data_fresh` | boolean | 필수 | 아니오 | 상태 값이 TTL 안에서 확인됐는지 |
| `elevators[].unknown_reason` | string | 필수 | **예** | `null`(확인됨) / `"NO_DATA"`(상태 행 없음) / `"STALE"`(TTL 초과) |
| `elevators[].observed_at` | string | 필수 | **예** | 원본 측정 시각. OA-15994에는 없어서 현재 항상 `null` (9절) |
| `elevators[].fetched_at` | string | 필수 | **예** | 우리가 가져온 시각(KST). 상태 행이 없으면 `null` |

---

## 5. 엘리베이터 상태 목록 — `GET /api/elevators/status`

### 5.1 요청

| 파라미터 | 위치 | 필수 | 타입 | 규칙 |
|---|---|---|---|---|
| `status` | query | 선택 | string | `"가동중"` 또는 `"가동 안됨"`. 그 외는 422 |
| `line` | query | 선택 | string | `"1"`~`"8"`. 그 노선을 지나는 역의 설비만 남긴다. 그 외는 422 |

요청 예시: `GET /api/elevators/status?status=가동%20안됨&line=2`

- `status`를 주면 **확인된 설비 중 해당 값과 일치하는 것만** 반환한다. 정보 없음 설비는 어떤 상태 필터에도 포함되지 않는다.
- `status`를 주지 않으면 모든 EV 설비를 반환한다. 정보 없음 설비도 포함한다.
- `line`은 설비가 아니라 **역이 지나는 노선**으로 거른다(OA-15994에 설비의 노선 정보가 없다). 환승역의 설비는 환승하는 모든 노선의 필터에 들어간다. 조건에 맞는 설비가 없으면 200과 `[]`다. `status`와 함께 쓰면 둘 다 만족하는 설비만 남는다.
- 미확인 설비(NO_DATA 또는 STALE)가 한 개라도 있으면 필터 적용 여부와 무관하게 `limitations`에 `"일부 설비의 상태 정보가 없거나 오래되어 현재 상태를 확인하지 못함"`이 포함된다.

### 5.2 응답 (200)

```json
{
  "elevators": [
    {
      "elevator_id": 102,
      "elevator_name": "승강기)엘리베이터-서울(1)역 섬식(상) 5-1 내부#1",
      "station_id": 1,
      "station_name": "서울역",
      "lines": [{"line_id": "1", "line_name": "1호선"}, {"line_id": "4", "line_name": "4호선"}],
      "status": "가동 안됨",
      "data_fresh": true,
      "unknown_reason": null,
      "observed_at": null,
      "fetched_at": "2026-11-13T14:00:00+09:00"
    }
  ],
  "count": 1,
  "limitations": ["일부 설비의 상태 정보가 없거나 오래되어 현재 상태를 확인하지 못함"]
}
```

> `limitations`는 시스템 전체 설비 중 미확인(NO_DATA, STALE)이 있으면 채워진다. 필터로 미확인 설비를 결과에서 제외해도 limitations는 그대로다. 모든 설비가 신선하게 확인된 경우에만 `[]`이다.

### 5.3 필드

| 필드 | 타입 | 필수 | nullable | 설명 |
|---|---|---|---|---|
| `elevators` | array | 필수 | 아니오 | 설비 목록. 0건이면 `[]` |
| `elevators[].elevator_id` | integer | 필수 | 아니오 | 내부 설비 ID |
| `elevators[].elevator_name` | string | 필수 | 아니오 | 설비명 |
| `elevators[].station_id` | integer | 필수 | 아니오 | 소속 역 ID |
| `elevators[].station_name` | string | 필수 | 아니오 | 소속 역명 |
| `elevators[].lines` | array | 필수 | 아니오 | 소속 역이 지나는 **전체** 노선(`line_id`, `line_name`, 호선 오름차순). `line` 필터와 무관하다. 설비 자체의 노선이 아니다 |
| `elevators[].status` | string | 필수 | **예** | 4.4절과 같다 |
| `elevators[].data_fresh` | boolean | 필수 | 아니오 | 4.4절과 같다 |
| `elevators[].unknown_reason` | string | 필수 | **예** | 4.4절과 같다 |
| `elevators[].observed_at` | string | 필수 | **예** | 4.4절과 같다 |
| `elevators[].fetched_at` | string | 필수 | **예** | 4.4절과 같다 |
| `count` | integer | 필수 | 아니오 | `elevators`의 길이 |
| `limitations` | array[string] | 필수 | 아니오 | 0.4절 |

### 5.4 오류

| 상황 | 상태 | `error.code` |
|---|---|---|
| `status`가 허용 값이 아님 (예: `고장`, `보수중`) | 422 | `VALIDATION_ERROR` |

### 5.5 역 단위 상태 목록 — `GET /api/stations/status` (D20)

지도에서 역마다 색을 칠하기 위한 목록이다. FE가 판정 규칙(6절)을 다시 구현하지 않도록, 역별 API(`GET /api/stations/{station_id}/elevators`)의 `station_status`와 **같은 코드**로 판정한 값을 한 번에 돌려준다.

| 파라미터 | 위치 | 필수 | 타입 | 규칙 |
|---|---|---|---|---|
| `line` | query | 선택 | string | `"1"`~`"8"`. 그 노선을 지나는 역만 남긴다. 그 외는 422 `VALIDATION_ERROR` |

- 역마다 정확히 한 건이고 `station_id` 오름차순이다. 엘리베이터가 없는 역도 포함하며 `status: null`, 개수 0이다(6.2절 `T = 0`).
- `stations[].station_status`는 4.4절의 `station_status`와 같은 필드(`status`, `data_fresh`, `total_elevator_count`, `unknown_elevator_count`)이고 6.2절 규칙을 따른다. 역별 API와 값이 일치하는지 테스트로 확인한다(`tests/test_station_status_list.py`).
- `lines`는 역이 지나는 **전체** 노선이며 `line` 필터와 무관하다(5.3절과 같다).
- `limitations`: 시스템 전체 설비 중 미확인(NO_DATA, STALE)이 있으면 `"일부 설비의 상태 정보가 없거나 오래되어 현재 상태를 확인하지 못함"`(필터와 무관, 5.1절과 같은 기준). `line=1`이거나 결과에 1호선 역이 있으면 1호선 문구(D11). 역별 `limitations`(문구 1~3)는 담지 않으므로 FE는 `status`·`data_fresh`로 색과 표시를 정한다. 6.2절의 FE 표시 규칙(`data_fresh: false`를 단정하지 않기, `null`은 "정보 없음")이 그대로 적용된다.
- 응답 예시는 테스트용 임시 DB(역 10개 규칙 확인용 데이터)에서 `TestClient`로 `GET /api/stations/status?line=2`를 호출해 얻은 실제 출력 중 앞 3개 역만 남긴 것이다(실제 역 데이터·`data/dev.db` 값이 아니다). 실제 운영 데이터로 호출한 결과는 (미검증).

```json
{
  "stations": [
    {
      "station_id": 2,
      "station_name": "시청",
      "lines": [
        {
          "line_id": "1",
          "line_name": "1호선"
        },
        {
          "line_id": "2",
          "line_name": "2호선"
        }
      ],
      "station_status": {
        "status": null,
        "data_fresh": false,
        "total_elevator_count": 2,
        "unknown_elevator_count": 2
      }
    },
    {
      "station_id": 3,
      "station_name": "강남",
      "lines": [
        {
          "line_id": "2",
          "line_name": "2호선"
        }
      ],
      "station_status": {
        "status": "AVAILABLE",
        "data_fresh": true,
        "total_elevator_count": 2,
        "unknown_elevator_count": 0
      }
    },
    {
      "station_id": 5,
      "station_name": "교대(법원, 검찰청)",
      "lines": [
        {
          "line_id": "2",
          "line_name": "2호선"
        },
        {
          "line_id": "3",
          "line_name": "3호선"
        }
      ],
      "station_status": {
        "status": "UNAVAILABLE",
        "data_fresh": true,
        "total_elevator_count": 1,
        "unknown_elevator_count": 0
      }
    }
  ],
  "count": 6,
  "limitations": [
    "일부 설비의 상태 정보가 없거나 오래되어 현재 상태를 확인하지 못함",
    "1호선은 일부 구간의 역만 포함되어 있어 전체 구간을 포함하지 않음"
  ]
}
```

---

## 6. 상태 판정 규칙

### 6.1 설비 상태 (Backend 내부 → 응답)

**원본 → 내부 매핑 (OA-15994 `USE_YN`, 실제 응답 기준)**

| 원본 `USE_YN` | 내부 `status` (`elevator_status.status`) | 응답 `status` |
|---|---|---|
| `사용가능` | `가동중` | `가동중` (데이터가 신선할 때만) |
| `보수중` | `가동 안됨` | `가동 안됨` (데이터가 신선할 때만) |
| 그 외 값 | 저장하지 않는다 | `null`, `unknown_reason: "NO_DATA"` |

- 원본 `USE_YN`은 `사용가능`, `보수중` 두 가지만 확인됐다(`docs/api-notes.md` 5절).
- `보수중`을 `가동 안됨`으로 보는 것은 Issue #14의 합의다.
- 원문은 `elevator_status.source_status_text`에 보관한다. 응답에는 원문을 노출하지 않는다.

**신선도 (`data_fresh`)**

- 판정 기준은 `fetched_at`이다. OA-15994 응답에는 원본 측정 시각(`observed_at`)이 없어서 `observed_at`은 기준으로 쓰지 않는다.
- `data_fresh = (현재 시각 − fetched_at) ≤ TTL`
- **TTL 기본값은 120분(2시간)이며 갱신 주기 미검증에 따른 임시 기본값이다(D1).** 설정 키 `ELEVATOR_STATUS_TTL_MINUTES`로 바꿀 수 있고, 허용 범위는 1~1440분이다.
  - `docs/api-notes.md`의 2시간은 매시 갱신 기준의 추정(미검증)이며 참고값이다.
  - 갱신 주기를 확인한 뒤 TTL을 확정한다(미검증).

**설비 응답 판정**

| 상황 | `status` | `data_fresh` | `unknown_reason` |
|---|---|---|---|
| 상태 행이 있고 TTL 안 | `가동중` 또는 `가동 안됨` | `true` | `null` |
| 상태 행이 있고 TTL 초과 | `null` | `false` | `"STALE"` |
| 상태 행이 없음 (수집 실패·매칭 실패 포함) | `null` | `false` | `"NO_DATA"` |

### 6.2 역 상태 (`station_status`)

**판정 대상은 확인된 설비뿐이다.** 정보 없음·오래된 설비는 상태 값으로 세지 않는다.

- `T` = 역의 EV 설비 수 (`total_elevator_count`)
- `U` = 정보 없음 또는 오래된 설비 수 (`unknown_elevator_count`)
- `C` = 확인된 설비 수 = `T − U`

| 상황 | `status` | `data_fresh` | `unknown_elevator_count` | `limitations` |
|---|---|---|---|---|
| `C` = `T`이고 전부 `가동중` | `AVAILABLE` | `true` | `0` | `[]` |
| `C` = `T`이고 `가동중`과 `가동 안됨`이 섞임 | `PARTIAL` | `true` | `0` | `[]` |
| `C` = `T`이고 전부 `가동 안됨` | `UNAVAILABLE` | `true` | `0` | `[]` |
| `0 < C < T`이고 확인된 설비가 전부 `가동중` | `AVAILABLE` | `false` | `U` | 아래 문구 |
| `0 < C < T`이고 확인된 설비가 섞임 | `PARTIAL` | `false` | `U` | 아래 문구 |
| `0 < C < T`이고 확인된 설비가 전부 `가동 안됨` | `UNAVAILABLE` | `false` | `U` | 아래 문구 |
| `C = 0`이고 `T > 0` (전부 정보 없음) | `null` | `false` | `U` (= `T`) | 아래 문구 2 |
| `T = 0` (엘리베이터 없음) | `null` | `false` | `0` | 아래 문구 3 (D3) |

- 문구 1: `"일부 설비 상태가 확인되지 않아 역 전체 상태를 단정할 수 없음"`
- 문구 2: `"확인 가능한 엘리베이터 상태가 없어 역 상태를 판정할 수 없음"`
- 문구 3: `"엘리베이터 정보가 없어 역 상태를 판정할 수 없음"`

**FE 표시 규칙 (필수)**

- `data_fresh`가 `false`인 역 상태를 **"이용 가능", "정상 운행", "엘리베이터 사용 가능"**처럼 단정하는 문구로 표시하면 안 된다.
- `AVAILABLE`이라도 `data_fresh`가 `false`면 "확인된 설비 기준으로 가동중"처럼 범위를 밝혀서 표시한다 (R1).
- `status`가 `null`이면 "정보 없음"으로 표시한다.

### 6.3 정상으로 추정하지 않는 원칙

- 상태 행이 없는 설비를 `가동중`으로 채우지 않는다.
- TTL을 넘은 값을 현재 상태처럼 보여 주지 않는다. 응답에서 `status`를 `null`로 바꾸고 `unknown_reason: "STALE"`로 표시한다.
- 확인된 설비가 없으면 역 상태는 `null`이다.
- 수집 실패 시 기존 상태 행을 유지하되 `fetched_at`은 갱신하지 않는다(#25). 시간이 지나면 자연스럽게 `STALE`이 된다.

---

## 7. 응답 예시 — 공통 오류

**404 — 없는 역 ID**

```json
{"error": {"code": "NOT_FOUND", "message": "요청한 리소스를 찾을 수 없습니다."}}
```

**422 — 잘못된 상태 필터**

```json
{"error": {"code": "VALIDATION_ERROR", "message": "요청 값이 올바르지 않습니다."}}
```

**405 — 허용되지 않은 메서드**

```json
{"error": {"code": "METHOD_NOT_ALLOWED", "message": "허용되지 않은 메서드입니다."}}
```

**500 — 서버 내부 오류** (내부 정보 없음)

```json
{"error": {"code": "INTERNAL_ERROR", "message": "서버 내부 오류가 발생했습니다."}}
```

---

## 8. 후속 계약 (예약)

아래 API는 구현되어 있고 8.1~8.4절에 초안 계약이 있다(FE/AI 리뷰 전). 표는 확정 Issue와 공통 규칙이다.

| API | 확정 Issue | 이미 정해진 공통 규칙 |
|---|---|---|
| `GET /api/stations/{station_id}/alternatives` | #33 | `limitations` 항상 포함. 실제 접근 가능성은 미검증이라고 명시 |
| `GET /api/routes` | #37 | 8.4절. `estimated: true`, `limitations` 포함. 경로 없음은 404 |
| `POST /api/departure-time` | #39 | `estimated`, `limitations` 포함. 계산 결과를 DB에 저장하지 않음 |
| `GET /api/stations/{station_id}/arrivals` | #40, #41 | 8.3절. 외부 실패 시 "미확인" 표시. 이전 캐시를 현재 정보처럼 보여 주지 않음 |

### 8.1 대체역 `GET /api/stations/{station_id}/alternatives` (#33, 초안 — FE/AI 리뷰 전)

- 응답: `station`, `station_status`(4.4절과 같음), `alternatives[]`, `count`, `search_depth`, `empty_reason`(없으면 `null`), `limitations`.
- `alternatives[]`: `station_id`, `station_name`, `lines`, `distance`(간선 수), `same_line`(대상 역과 노선을 하나라도 공유), `station_status`.
- 후보는 `station_edges`의 ADJACENT 간선을 BFS로 탐색한다(깊이는 `ALTERNATIVE_SEARCH_DEPTH`, 기본 2·임시). 간선이 없으면 후보도 없으며 인접·환승 관계를 추정하지 않는다.
- 가동중 설비가 확인된 역(`AVAILABLE`, `PARTIAL`)만 후보다. 대상 역이 `AVAILABLE`이고 `data_fresh`면 후보 없이 `empty_reason`을 준다.
- 정렬: 같은 노선 먼저, 거리, `AVAILABLE` 먼저, 신선한 데이터 먼저, `station_id`.
- `limitations`에는 항상 "실제 접근 가능한 경로와 환승 가능 여부는 검증되지 않음"이 있다. 오류 형식은 0.6절과 같다.

### 8.2 출발 권장 시각 `POST /api/departure-time` (#39, 필드·기본값 확정 — FE/AI 리뷰 전)

도착 희망 시각에서 거꾸로 계산해 권장 출발 시각을 준다. 계산은 `app/services/departure_time.py`의 순수 함수를 그대로 쓴다. **경로 탐색으로 이동 시간을 정하지 않고, 실시간 도착 정보와 AI 추천을 쓰지 않는다.** 결과를 저장하지 않는다. `from_station_id`와 `to_station_id`를 주지 않으면 DB도 읽지 않고 시간표도 쓰지 않는다.

**시간표 기반 열차 선택 (#38).** 역 id를 주고, 경로가 **환승 없는 한 노선**이며, 시간표(`train_schedules`)에서 맞는 열차를 찾으면 그 열차의 **계획 시각**으로 탑승·하차 시각을 맞춘다(`selected_train`이 채워지고 `timetable_used: true`). 도착 희망 시각에서 출구 이동을 뺀 시각까지 하차역에 닿는 열차 중 탑승역을 가장 늦게 출발하는 일반 열차(급행 제외)를 고른다. 이때 `travel_minutes`는 검증만 하고 계산에 쓰지 않으며(`inputs`에는 입력값이 그대로 남는다), 타임라인의 `RIDE`는 열차의 탑승~하차 시각(분 단위 반올림)이다. 방향은 경로의 첫 구간(탑승역→다음 역)을 같은 열차가 짧은 간격(30분 이내)으로 서는 방향으로 추정한다(상·하행·내·외선 의미는 쓰지 않는다). 서비스일 종류는 날짜의 요일로만 정한다(공휴일 데이터 없음). 자정을 넘긴 열차(24시 이후)는 전날 서비스일의 시간표에서 찾는다. 환승 경로, 방향을 정하지 못함, 시간표 행 없음, 맞는 열차 없음이면 **기존 추정 계산을 그대로 쓴다**(`timetable_used: false`, `selected_train: null`, 이유를 `limitations`에 추가).

`권장 출발 = 도착 희망 − (집→역 도보 + 역 진입 + 여유 시간 + 예상 이동 시간 + 출구 이동)`

**요청 (JSON 본문).** 알 수 없는 필드는 422다. 분 값은 정수만 받는다(문자열·실수·bool은 422).

| 필드 | 필수 | 타입 | 규칙 | 기본값 |
|---|---|---|---|---|
| `arrival_at` | 필수 | string | 오프셋이 있는 ISO 8601, 초 단위(`2026-11-13T09:00:00+09:00`, `...Z`). 오프셋 없음·날짜만·소수 초는 422 | - |
| `travel_minutes` | 필수 | integer | 1~720. **외부에서 받은 예상 이동 시간**(경로 탐색으로 검증하지 않음) | - |
| `buffer_minutes` | 선택 | integer | 0~240. 승강장 대기·여유 시간(탑승 전에 둠) | **5** (추정값, 아래 참고) |
| `walk_to_station_minutes` | 선택 | integer | 0~120. 집→역 도보 | 설정 `WALK_TO_STATION_MINUTES` (**7**) |
| `station_entry_minutes` | 선택 | integer | 0~120. 역 진입 | 설정 `STATION_ENTRY_MINUTES` (**3**) |
| `exit_walk_minutes` | 선택 | integer | 0~120. 하차 후 출구 이동 | 설정 `EXIT_WALK_MINUTES` (**3**) |
| `from_station_id` | 선택 | integer | 출발역 `station_id`. `to_station_id`와 **함께** 주어야 한다(하나만 주면 422). 없는 역은 404, 같은 역끼리는 422 | - |
| `to_station_id` | 선택 | integer | 도착역 `station_id` | - |

- 선택 필드를 생략하거나(`buffer_minutes`) `null`로 주면(나머지) 기본값을 쓰고, 쓴 필드 이름을 `defaults_applied`에 담는다. `buffer_minutes`를 `0`으로 명시하면 기본값을 쓴 것이 아니다. 5·7·3·3분은 **검증되지 않은 추정값**이다.
- `buffer_minutes` 기본 5분은 배차 간격 3분과 교통약자 여유 2분을 더한 값이다(팀 결정 2026-11-14, AI 파트 제안). 열차 한 대를 놓쳐도 늦지 않도록 최악 대기를 기본으로 두며, FE와 챗봇이 같은 값을 쓰도록 BE 한 곳에서 정한다.
- `travel_minutes`는 계속 **외부 입력**이다. 경로 API(8.4절)의 `total_minutes`를 넘기는 것은 호출하는 쪽(FE, AI)이 한다. 이 API가 경로 API를 불러 값을 채우지 않는다. 역 id를 주면 경로는 경고 계산에만 쓴다.
- "이미 지났는지"는 서버의 현재 시각으로 판단한다. 현재 시각을 요청으로 받지 않는다.

**응답 (200).** 시각은 모두 KST(+09:00)다(0.2절).

```json
{
  "recommended_departure_at": "2026-11-13T23:17:00+09:00",
  "estimated_boarding_at": "2026-11-13T23:37:00+09:00",
  "estimated_alighting_at": "2026-11-14T00:07:00+09:00",
  "arrival_at": "2026-11-14T00:10:00+09:00",
  "total_minutes": 53,
  "date_changed": true,
  "already_past": false,
  "estimated": true,
  "timetable_used": false,
  "inputs": {"travel_minutes": 30, "buffer_minutes": 10, "walk_to_station_minutes": 7, "station_entry_minutes": 3, "exit_walk_minutes": 3},
  "defaults_applied": ["walk_to_station_minutes", "station_entry_minutes", "exit_walk_minutes"],
  "timeline": [
    {"kind": "WALK_TO_STATION", "label": "집에서 역까지 도보", "start_at": "2026-11-13T23:17:00+09:00", "end_at": "2026-11-13T23:24:00+09:00", "minutes": 7},
    {"kind": "STATION_ENTRY", "label": "역 진입", "start_at": "2026-11-13T23:24:00+09:00", "end_at": "2026-11-13T23:27:00+09:00", "minutes": 3},
    {"kind": "BUFFER", "label": "여유 시간", "start_at": "2026-11-13T23:27:00+09:00", "end_at": "2026-11-13T23:37:00+09:00", "minutes": 10},
    {"kind": "RIDE", "label": "열차 이동(외부 입력 예상 시간)", "start_at": "2026-11-13T23:37:00+09:00", "end_at": "2026-11-14T00:07:00+09:00", "minutes": 30},
    {"kind": "EXIT_WALK", "label": "출구 이동", "start_at": "2026-11-14T00:07:00+09:00", "end_at": "2026-11-14T00:10:00+09:00", "minutes": 3}
  ],
  "elevator_warning": {"status": "UNCONFIRMED", "items": [], "message": "역·설비 상태 정보가 입력되지 않아 엘리베이터 경고를 확인하지 못함"},
  "selected_train": null,
  "limitations": [
    "실제 열차 시간표를 사용하지 않은 추정 계산이며 탑승 가능한 열차는 확인되지 않음",
    "예상 이동 시간은 외부에서 입력한 값이며 경로 탐색으로 검증되지 않음",
    "도보·역 진입·출구 이동 시간은 추정값이며 개인과 상황에 따라 다를 수 있음",
    "역·설비 상태 정보가 입력되지 않아 엘리베이터 경고를 확인하지 못함"
  ]
}
```

| 필드 | 설명 |
|---|---|
| `recommended_departure_at` | 권장 출발 시각 |
| `estimated_boarding_at`, `estimated_alighting_at` | 탑승·하차 시각. `selected_train`이 없으면 시간표를 쓰지 않은 **추정**이고, 있으면 그 열차의 **계획 시각**이다. 어느 쪽이든 지연이 반영되지 않아 **탑승 확정 시각이 아니다** |
| `selected_train` | 시간표로 고른 열차. 고르지 못하면 `null`(**필드는 항상 있다**). 필드: `train_no`(원본 열차 번호), `boarding_at`, `alighting_at`(KST), `direction_inferred`(항상 `true`, 방향은 정차 순서로 추정) |
| `arrival_at` | 요청한 도착 희망 시각(KST) |
| `date_changed` | KST 날짜 기준으로 출발일과 도착일이 다르면 `true`(자정 이전 출발) |
| `already_past` | 권장 출발 시각이 서버 현재 시각보다 이전이면 `true`. 같으면 `false` |
| `estimated` / `timetable_used` | `estimated`는 항상 `true`. `timetable_used`는 열차를 시간표로 골랐을 때만 `true`(그때 `selected_train`이 있다) |
| `elevator_warning` | 8.4절과 같은 형식(`status`, `items[]`, `message`). 역 id가 없으면 `status: "UNCONFIRMED"`, `items: []`이며 **경고를 임의로 만들지 않는다**. 역 id를 주면 두 역 사이 경로 위 **모든 역**을 판정한다. 경로를 찾지 못하면 오류가 아니라 `UNCONFIRMED`와 `items: []`다 |
| `limitations` | 추정 계산이면 위 3개, 시간표로 열차를 골랐으면 0.4절의 시간표 문구 5개(선택·방향 추정·입력 이동 시간 미사용·도보 등 추정·공휴일 정보 없음). 시간표를 시도했으나 쓰지 못한 경우(맞는 열차 없음, 환승 경로)는 추정 계산 문구에 그 이유가 추가된다. 그 뒤에 경고 문구(`elevator_warning.message`)가 들어간다. `CLEAR`이면 경고 문구는 넣지 않는다. `already_past`가 `true`면 "추천 출발 시각이 현재 시각보다 이미 지났음"이 추가된다 |

**오류.** 0.6절 형식이다. 잘못된 시각·형식, 정수가 아닌 값, 범위 밖 값, 알 수 없는 필드, 깨진 JSON, 표현 가능한 날짜 범위를 벗어난 시각은 422 `VALIDATION_ERROR`, GET 등 다른 메서드는 405다.

**AI/FE 영향(비breaking).** 응답에 `selected_train`(기본 `null`)이 추가됐다. 역 id를 준 요청에서는 `timetable_used`가 `true`가 될 수 있고, 그때 `limitations` 구성과 `inputs.travel_minutes`의 의미(입력값 그대로, 계산에 쓰지 않음)와 `RIDE` 구간 길이가 달라진다. `timetable_used`를 항상 `false`로 가정한 코드는 고쳐야 한다. 열차 방향·번호는 시간표 기준 추정이며 확정 정보가 아니다.

**미구현(보류).** 환승 경로의 시간표 열차 선택(첫 구간만 쓰지 않고 추정 계산으로 돌린다), 공휴일 시간표 선택, 실시간 도착의 외부 출처 연동(#40, #41), AI 최적 경로 추천.

---

### 8.3 실시간 도착 정보 `GET /api/stations/{station_id}/arrivals` (#40, #41, 초안 — FE/AI 리뷰 전)

**외부 출처는 연동되지 않았다.** OA-12764의 호출 주소, 서비스 이름, 응답 필드는 저장소에서 검증되지 않았다(미검증). 그래서 Backend는 자체 정규화 모델과 공급자 인터페이스만 두고, 공급자가 없으면 항상 `UNCONFIRMED`를 돌려준다. 과거 데이터나 만들어 낸 값은 돌려주지 않는다.

- 응답: `station`, `arrival_status`, `unconfirmed_reason`, `arrivals[]`, `count`, `fetched_at`, `cache_hit`, `cache_ttl_seconds`, `limitations`.
- `arrival_status`: `CONFIRMED`(도착 1건 이상), `EMPTY`(조회는 성공했으나 도착 정보 없음), `UNCONFIRMED`(확인하지 못함).
- `unconfirmed_reason`: `UNCONFIRMED`일 때만 `PROVIDER_NOT_CONFIGURED`(출처 미연동) 또는 `PROVIDER_ERROR`(외부 오류·시간 초과·해석 불가). 그 밖에는 `null`.
- `arrivals[]`(`eta_seconds` 순): `line`(nullable), `direction`(nullable), `destination`(없으면 `null`), `eta_seconds`(`fetched_at` 시점의 남은 시간), `estimated_arrival_at`(`fetched_at + eta_seconds`, KST).
- `fetched_at`: 외부에서 가져온 시각(KST). `UNCONFIRMED`이면 `null`. 캐시에서 준 경우 `cache_hit`이 `true`이고 `fetched_at`은 원래 가져온 시각이다.
- 캐시: 메모리, 성공한 조회만 `ARRIVAL_CACHE_TTL_SECONDS`(기본 30, 1~300) 동안 보관한다. DB 테이블은 없다. 만료 항목은 조회·저장 때 제거하고 항목 수는 512개로 제한한다. **외부 조회가 실패하면 이전 캐시 값을 돌려주지 않는다.**
- 오류: 없거나 0 이하인 역 ID는 404, 정수가 아니면 422(0.6절). 외부 실패는 오류가 아니라 200 + `UNCONFIRMED`다.
- 미결정: 출발 시각 API(8.2절)에 실시간 정보를 넣을지(#41). 지금은 넣지 않았다.

### 8.4 경로 조회 `GET /api/routes` (#36, #37, 초안 — FE/AI 리뷰 전)

두 역 사이의 최단 경로를 준다. **소요 시간은 고정 가중치를 쓴 추정값**이고, 엘리베이터 상태는 경로를 고르는 데 쓰지 않으며 경고로만 붙인다. 결과를 저장하지 않는다.

**요청 (query).**

| 파라미터 | 필수 | 타입 | 규칙 |
|---|---|---|---|
| `from` | 필수 | integer | 출발역 `station_id`. 정수가 아니면 422, 0 이하이거나 없는 역이면 404 |
| `to` | 필수 | integer | 도착역 `station_id`. 위와 같다 |

요청 예시: `GET /api/routes?from=3&to=7`

**계산 규칙.**
- 그래프의 노드는 역-노선이고 간선은 `station_edges`의 `ADJACENT`(역 간)와 `TRANSFER`(환승)다. 양방향으로 쓴다.
- 가중치: 역 간 `ROUTE_SEGMENT_MINUTES`(기본 **2분**), 환승 `ROUTE_TRANSFER_MINUTES`(기본 **5분**). 검증되지 않은 추정값이다.
- 출발역의 모든 노선에서 시작하고 도착역의 어느 노선에 닿아도 된다. 따라서 출발역에서 노선을 고르는 데 환승 시간은 들지 않는다.
- 같은 비용이면 항상 같은 경로를 돌려준다.
- 인접 간선은 열차 시간표에서 추출한다(`app.cli.derive_edges`, source `OA-101`). 공식 인접역 자료가 아니다. 환승 간선은 공식 기록(OA-13290)이다.

**응답 (200).**

```json
{
  "from_station": {"station_id": 3, "station_name": "강남"},
  "to_station": {"station_id": 7, "station_name": "종로3가"},
  "stations": [
    {"station_id": 3, "station_name": "강남", "lines": [{"line_id": "2", "line_name": "2호선"}], "is_transfer": false,
     "station_status": {"status": "UNAVAILABLE", "data_fresh": true, "total_elevator_count": 1, "unknown_elevator_count": 0}},
    {"station_id": 5, "station_name": "교대(법원, 검찰청)", "lines": [{"line_id": "2", "line_name": "2호선"}, {"line_id": "3", "line_name": "3호선"}], "is_transfer": true,
     "station_status": {"status": "PARTIAL", "data_fresh": true, "total_elevator_count": 2, "unknown_elevator_count": 0}},
    {"station_id": 7, "station_name": "종로3가", "lines": [{"line_id": "3", "line_name": "3호선"}], "is_transfer": false,
     "station_status": {"status": "AVAILABLE", "data_fresh": true, "total_elevator_count": 1, "unknown_elevator_count": 0}}
  ],
  "transfer_count": 1,
  "total_minutes": 9,
  "estimated": true,
  "timeline": [
    {"kind": "RIDE", "label": "2호선 강남 → 교대(법원, 검찰청)", "minutes": 2, "line_id": "2", "line_name": "2호선",
     "from_station": {"station_id": 3, "station_name": "강남"}, "to_station": {"station_id": 5, "station_name": "교대(법원, 검찰청)"}, "stops": 1},
    {"kind": "TRANSFER", "label": "교대(법원, 검찰청)에서 2호선 → 3호선 환승", "minutes": 5,
     "station": {"station_id": 5, "station_name": "교대(법원, 검찰청)"}, "from_line_id": "2", "to_line_id": "3"},
    {"kind": "RIDE", "label": "3호선 교대(법원, 검찰청) → 종로3가", "minutes": 2, "line_id": "3", "line_name": "3호선",
     "from_station": {"station_id": 5, "station_name": "교대(법원, 검찰청)"}, "to_station": {"station_id": 7, "station_name": "종로3가"}, "stops": 1}
  ],
  "elevator_warning": {
    "status": "WARNING",
    "items": [
      {"station_id": 3, "station_name": "강남", "kind": "UNAVAILABLE", "message": "확인된 엘리베이터가 모두 가동 안됨"},
      {"station_id": 5, "station_name": "교대(법원, 검찰청)", "kind": "PARTIAL", "message": "일부 엘리베이터가 가동 안됨"}
    ],
    "message": "경로 위 일부 역에서 엘리베이터가 가동 안됨이거나 일부만 가동중임"
  },
  "limitations": [
    "소요 시간은 역 간 이동과 환승에 고정 시간을 가정한 추정값이며 실제 열차 시간표와 다를 수 있음",
    "역 간 연결은 열차 시간표에서 추출한 것이며 공식 인접역 자료로 검증되지 않음",
    "엘리베이터만으로 이동할 수 있는 환승·출입 동선인지는 검증되지 않음"
  ]
}
```

| 필드 | 설명 |
|---|---|
| `stations[]` | 경로 순서의 역. 환승해도 같은 역은 한 번만 나온다. `lines`는 역이 지나는 전체 노선, `station_status`는 6.2절 규칙의 역 상태다 |
| `stations[].is_transfer` | 이 역에서 노선을 갈아타면 `true` |
| `transfer_count` | 환승 횟수 |
| `total_minutes` | `timeline[].minutes`의 합. **추정값**이다 |
| `timeline[]` | 연속한 같은 노선 이동은 `RIDE` 하나로 묶는다(`stops`는 역 간 구간 수). 환승은 `TRANSFER`다. `RIDE`에는 `line_id`, `from_station`, `to_station`, `stops`가, `TRANSFER`에는 `station`, `from_line_id`, `to_line_id`가 채워지고 나머지 필드는 `null`이다 |
| `elevator_warning.status` | `WARNING`(가동 안됨·일부 가동 안됨 역이 있음), `UNCONFIRMED`(경고는 없지만 확인하지 못했거나 일부만 확인된 역이 있음), `CLEAR`(경로 위 모든 역이 확인된 설비 기준으로 모두 가동중) |
| `elevator_warning.items[]` | 경로 순서로 주의가 필요한 역만 담는다. `kind`는 `UNAVAILABLE`, `PARTIAL`, `UNCONFIRMED`. `CLEAR`면 `[]` |
| `limitations` | 추정 문구는 항상, 시간표 추출·임시 간선 문구는 해당 간선을 쓴 경우만, 접근성 미검증 문구는 항상. 1호선 역이 포함되면 0.4절 문구가 추가된다 |

- **정보가 없는 역을 정상으로 추정하지 않는다.** 설비 정보가 없거나 오래된 역은 `UNCONFIRMED`다. `CLEAR`도 "이용 가능"이 아니라 "확인된 설비 기준"이라고 쓴다(6.3절).

**오류.** 0.6절 형식이다.

| 상황 | 상태 | `error.code` |
|---|---|---|
| `from` 또는 `to`가 없거나 정수가 아님 | 422 | `VALIDATION_ERROR` |
| 출발역과 도착역이 같음 | 422 | `VALIDATION_ERROR` |
| 0 이하이거나 없는 역 | 404 | `NOT_FOUND` |
| 두 역을 잇는 경로가 없음(연결 데이터가 끊긴 경우 포함) | 404 | `NOT_FOUND` |

## 9. 실제 데이터에서 확인되지 않은 항목 (미검증)

| 항목 | 현재 상태 |
|---|---|
| 역 마스터(1~8호선 역명·노선·외부 코드) | 저장소에 없다. 공식 출처 확인 후 적재한다(#1, #19). 이 문서의 역·노선 예시는 형식만 보여 준다 |
| 1호선 구간 | 현재 시드에는 1호선 10개 역만 있다. 나머지 구간은 별도 조사 대상이며 미확보다. 1~8호선 전체를 확보했다는 뜻이 아니다 (`data/seed/SOURCES.md`) |
| 환승 관계 | 공식 환승 기록(OA-13290)으로 확인된 34개 그룹만 한 역으로 합친다(서울역은 근거를 검증한 수동 확정). 그 밖의 역은 노선별로 따로 있으며 `is_transfer`는 `false`다. 시드에 없는 1호선 역의 환승은 확인하지 못했다 (`data/seed/SOURCES.md`) |
| 역 좌표 | 출처 미정. `latitude`, `longitude`는 `null`일 수 있다 |
| 설비 노선 소속 | OA-15994에 노선 컬럼이 없다. 설비의 노선은 알 수 없고, 상태 목록의 `lines`는 설비가 속한 **역**의 노선이다 |
| 인접 간선 | 공식 인접역 자료가 없다. 열차 시간표(OA-101)에서 같은 열차의 정차 순서로 추출한다. 정차 묶음은 (노선, 요일, 방향, 열차 번호)로 나누고, 급행 표시가 한 행이라도 있거나 서로 다른 역의 정차 시각이 같은 열차는 통째로 제외한다(제외한 행 앞뒤를 잇지 않는다). 지지 수는 서로 다른 열차 번호 수이며 2개 이상이어야 한다(#72). 시간표의 `WEEK_TAG`·`INOUT_TAG` 의미는 추정이다 |
| 경로 소요 시간 | 역 간 2분·환승 5분은 가정이다. 실제 시간표나 도보 환승 시간이 아니다 |
| `observed_at` | OA-15994 응답에 원본 측정 시각이 없다. 현재 항상 `null`이다 |
| 갱신 주기 | "매시"는 공공데이터 설명 기준이며 응답으로 확인하지 못했다 |
| TTL | 기본 120분(임시, 설정 가능, 1~1440분). 갱신 주기 미검증 (D1) |
| 엘리베이터 종류 | 응답의 `ELVTR_SE` 중 `EV`만 저장한다. `ES`(에스컬레이터), `WL`(휠체어리프트), `MW`(무빙워크)는 제외한다 |
| 호출 한도 | 미측정 |
| 실시간 도착(OA-12764) | 호출 주소·서비스 이름·응답 필드·갱신 주기·호출 한도 모두 미검증. 실제 연동 보류 |

---

## 10. 결정 사항과 검토 항목

### 10.1 확정된 결정 (2026-11-13, 사용자 승인)

| # | 항목 | 확정 내용 | 반영 위치 |
|---|---|---|---|
| D1 | TTL | 기본 120분(임시값, 2026-11-14 30분에서 변경), 설정으로 변경 가능. 갱신 주기 확인 후 확정 | 6.1절, 9절 |
| D2 | 일부 설비만 미확인인 역 | 판정 가능한 `status`를 유지하고 `data_fresh: false`, `unknown_elevator_count`, 표준 문구 1을 표시 | 6.2절 |
| D3 | 확인된 설비가 없는 역 | `status: null` (설비가 0개인 역 포함) | 6.2절 |
| D4 | 검색어 `q` | 앞뒤 공백 제거 후 1~20자. 범위 밖이면 422. 생략은 D19 | 2.1절 |
| D5 | 검색 결과 0건 | 200과 빈 배열 | 0.7절 |
| D6 | 엘리베이터 상태 필터 | `가동중`, `가동 안됨`만 허용. 그 외 값은 422 | 5.1절 |
| D7 | Issue #26의 `UNKNOWN` 역 상태 | `status: null`로 수정. #26 본문을 수정했다 | 6.2절 |
| D8 | 역 좌표 | 없으면 `null` 허용 | 3.3절 |
| D9 | `limitations` 문구 | 한국어 안내 문구 사용. 표준 문구는 0.4절 | 0.4절 |
| D10 | 역 목록 정렬·페이징 | 역명 오름차순, 동일 역명은 `station_id` 오름차순. MVP에서는 페이징 없음 | 2.3절 |
| D11 | 1호선 커버리지 | 1호선은 일부 구간만 확보했으므로 1호선 역이 포함된 응답의 `limitations`에 이를 명시 | 0.4절, 9절 |
| D12 | 역 상세의 0 이하 ID | `station_id`가 0 이하이면 404 `NOT_FOUND`. 정수가 아니면 422 `VALIDATION_ERROR` 유지 | 3.4절 |
| D13 | 좌표 `null` | 좌표가 확인되지 않은 역은 `latitude`, `longitude`를 `null`로 반환하고 임의로 채우지 않음 | 3.2절, 3.3절 |
| D14 | 서울 열린데이터광장 HTTP | 문서화된 주소가 HTTP(8088)뿐이므로 평문 HTTP를 기본 허용한다(2026-11-14). 키는 서버 `.env`에만 두고 로그·오류에는 가린다 | README |
| D15 | `buffer_minutes` 기본값 | 0분에서 **5분**으로 변경(2026-11-14). FE와 챗봇이 같은 출발 시각을 말하도록 BE 한 곳에서 정한다 | 8.2절 |
| D16 | 인접 간선 출처 | 열차 시간표(OA-101)에서 같은 열차의 정차 순서로 추출해 `station_edges`에 저장한다(2026-11-14). 요청마다 계산하지 않는다 | 8.4절, 9절 |
| D17 | 출발 시각 API의 이동 시간 | `travel_minutes`는 외부 입력을 유지한다. 역 id를 주면 경로 위 모든 역의 엘리베이터 경고를 계산한다(2026-11-14) | 8.2절 |
| D18 | 상태 목록의 노선 | `line` 필터와 `lines` 필드를 추가한다. 역이 지나는 노선이며 설비의 노선이 아니다(2026-11-14, AI 요청) | 5절 |
| D19 | 역 전체 목록 | `GET /api/stations`에서 `q`를 생략하면 전체 역(`line` 필터 적용). 명시한 빈 `q`는 422 유지(2026-11-15, 사용자 결정, #73) | 2.1절 |
| D20 | 역 단위 상태 목록 | 지도 색칠용 `GET /api/stations/status`. 판정은 역별 API와 같은 코드(`_judge`)를 쓴다. 설비 없는 역 포함, `line` 필터 지원(2026-11-15) | 5.5절 |

### 10.2 FE/AI 검토가 필요한 항목

아래 항목은 계약을 바꾸지 않는다. 화면 표시와 소비 방식을 확인하는 항목이다. **리뷰 전까지 Issue #14는 Open이다.**

| # | 담당 | 검토 질문 |
|---|---|---|
| R1 | FE | `data_fresh: false`인 역 상태(특히 `AVAILABLE`)를 어떤 문구로 표시할지. "이용 가능", "정상 운행" 같은 단정 표현은 금지(6.2절) |
| R2 | FE | `unknown_reason`(`NO_DATA`, `STALE`)과 `status: null`을 모두 "정보 없음"으로 표시할지 |
| R3 | FE | `status: null` 역(확인된 설비 없음, 설비 0개)의 표시 문구 |
| R4 | FE | 422 응답에 필드 상세가 없다. 폼 검증을 프런트에서 먼저 할지 |
| R5 | FE | 표준 limitations 문구(0.4절)를 그대로 쓸지, 화면에 맞춰 바꿀지 |
| R6 | FE | `status` 필터(`가동중`, `가동 안됨`)를 URL에 넣을 때 인코딩 처리 |
| R7 | FE | 좌표가 `null`일 때 지도 표시 방식 |
| R8 | FE | KST 시각(`+09:00`) 파싱과 표시 형식 |
| R9 | AI | 상태를 설명하는 문장을 만들 때 `data_fresh: false`와 `limitations`를 함께 전달하고, 정상 운행으로 요약하지 않을지 — **확인됨**(AI 답변 2026-11-14, BE #14 코멘트: 상태 `null`은 "확인할 수 없음", `data_fresh: false`면 "확인된 설비 기준", `limitations`는 코드로 덧붙임) |
| R10 | AI | `estimated`와 `limitations` 규칙을 경로·출발 시각 응답(#37, #39)에도 같은 방식으로 쓸지 — **확인됨**(AI 답변 2026-11-14: 같은 방식으로 씀. 경로·출발 시각의 AI 연동은 아직 전이라 실제 응답 문장은 연동 시점에 R13·R14로 확인) |
| R11 | BE | #26 구현 시 0절과 6절 규칙을 지키는지 확인 (#26 본문은 #14 규칙에 맞춰 수정함) |
| R12 | AI·팀 | 전체 역 목록이 필요하다(AI는 `GET /api/stations/{id}`를 1부터 순회해 우회, ID 연속 가정). `q` 생략 허용 vs 별도 목록 API. **해결(#73, D19)**: `q`를 생략하면 전체 목록을 준다. AI는 ID 순회 대신 `GET /api/stations` 한 번으로 전체 역을 읽을 수 있고 ID 연속 가정이 필요 없다. FE는 빈 `q=`를 보내지 않는다(422) |
| R13 | AI·FE | 경로 API(8.4절)의 `elevator_warning`과 `limitations`(추정·시간표 추출 간선·접근성 미검증)를 AI/FE가 그대로 전달하는지, 시연에서 경로를 확정 정보처럼 말하지 않는지 |
| R14 | AI | 출발 시각의 `estimated_boarding_at`은 확정 시각이 아니다(`timetable_used`가 `false`면 추정, `true`면 시간표 계획 시각이라 지연 미반영)라는 점과 `selected_train`의 방향이 추정이라는 점을 안내 문장에 반영하는지 |

R9·R10에 대한 AI의 답변은 BE #14 코멘트(2026-11-14)에서 확인했고 두 항목은 확인됨으로 닫는다.

### 10.3 알려진 한계 (팀 결정으로 한계로 두기로 한 것, 2026-11-15)

아래는 이번 시연 범위에서 고치지 않고 한계로 안내하는 항목이다.

- **막차 공백**: 도착 시각이 막차 이후면 막차를 골라 `total_minutes`는 타임라인 합(예: 40분)이고 `도착 − 권장 출발`과 다를 수 있다. 예: 강남→성수를 다음날 04:00 도착으로 요청하면 막차(00:38)를 고르며 도착과의 차이 217분(공백 177분)은 안내하지 않는다.
- **공휴일**: 공휴일 데이터가 없어 요일(평일·토·일)로만 서비스일을 정한다. 평일 공휴일에도 평일 시간표를 쓰며 `timetable_used`가 `true`가 될 수 있다(`limitations`로 안내).
- **지난 열차**: 현재 시각을 고려하지 않아 이미 지난 열차가 선택될 수 있다(`already_past`).
- **단방향 순환(6호선 응암순환)**: 연신내→독바위, 불광→역촌, 응암→구산은 방향이 `None`이라 기존 추정으로 폴백한다. 역방향 이동을 안내할 수 있다.
- **응암–역촌 간선 없음**: 시간표에서 응암 출발 행의 방향 값이 순환 구간과 달라(응암 출발은 방향 1, 역촌…구산→응암은 방향 2) 이 간선이 추출되지 않는다. 응암↔역촌 경로는 순환을 돌아가는 경로로 나올 수 있다.
- **간선 추출 검증 범위(#72)**: 규칙을 고친 뒤 운영 DB 사본에서 dry-run한 결과 기존 269개 간선과 같았다(추가·삭제 0). 그래도 공식 인접역 자료와 대조하지 않았고, 실제 엘리베이터 접근성과 시간표 완전성은 (미검증)이다. 실데이터 연결성은 수동 확인만 했고 자동 테스트는 시드 기준이다(#31).
- **외부 자료 확인 범위**: OA-22724(최단경로)는 호출하지 않았고, OA-101·OA-15994의 일일 호출 한도와 응답 시간은 측정하지 않았다(#15). `WEEK_TAG` 1·2·3을 평일·토·일/공휴일로 본 것은 데이터 건수로 한 추정이다(#35). 역 코드 REVIEW 2건(0321 충무로, 2538 청구)과 이름 표기 차이 5건은 `docs/api-notes.md`에 목록으로만 남겼다(#16).
- **실시간 도착 미연동**: 실시간 도착(OA-12764)은 연동하지 않아 도착 API는 항상 `UNCONFIRMED`다. 출발 시각 API에는 실시간 정보를 넣지 않기로 했다(#40, #41).
- **수집 실패 실험**: 외부 오류 시 DB와 `fetched_at`이 유지되는 것은 테스트로만 확인했고 실제 장애 상황에서는 실험하지 않았다(#44).
- **`--prune`·간선 필터**: 20% 상대 지지 임계값(`MIN_RELATIVE_SUPPORT`)은 설정할 수 없는 상수다. `--prune`은 부분 수집 DB에서 유효한 간선을 지울 수 있다(가드는 PR #75).
- **운영**: 호스팅 방식, SQLite 백업·영속, 외부 API 일일 호출 한도는 미정·미확인이다(`docs/demo-runbook.md`).
- **커밋 메시지 언어**: `AGENTS.md`는 영어 한 줄을 요구하지만 `main`의 최근 이력은 한국어다. 표준을 정하지 않았다.

### 10.4 Issue 상태

- **#14:** FE/AI 리뷰 전까지 Open 유지.
- **#26:** 본문을 #14 규칙에 맞췄다. `UNKNOWN` 상태 제거, 가동중·가동 안됨 용어 통일, TTL 임시 기본값 명시. Open.
- **#15:** Open 유지.
