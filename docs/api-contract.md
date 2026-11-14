# Backend API 계약

> 상태: 결정 사항 반영 (2026-11-13). **FE/AI 리뷰 전이며 Issue #14는 Open 상태다.** 10.2절 항목을 검토한다.
> 기준: Issue #14 본문에 합의된 상태 판정 규칙. 데이터 근거는 `docs/api-notes.md`.
> 표기: `(D번호)`는 확정된 결정(10.1절), `(R번호)`는 FE/AI 검토 항목(10.2절), `(미검증)`은 실제 데이터로 확인하지 못한 항목이다.
> JSON 예시의 역 ID, 시각, 설비 상태는 **형식을 보여 주는 예시**다. 실제 역 마스터 데이터는 아직 없다(#1, #19).

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

Issue #8의 업무 엔드포인트 7개와 헬스 체크다.

| # | Method | Path | 이 문서 | 구현 Issue | 상태 |
|---|---|---|---|---|---|
| 0 | GET | `/health` | 확정 | #13 | 구현 완료 |
| 1 | GET | `/api/stations` | 2절 | #21 | 계약 초안 |
| 2 | GET | `/api/stations/{station_id}` | 3절 | #22 | 계약 초안 |
| 3 | GET | `/api/stations/{station_id}/elevators` | 4절 | #28 | 계약 초안 |
| 4 | GET | `/api/elevators/status` | 5절 | #28 | 계약 초안 |
| 5 | GET | `/api/stations/{station_id}/alternatives` | 8절 (예약) | #33 | 후속 확정 |
| 6 | GET | `/api/routes` | 8절 (예약) | #37 | 후속 확정 |
| 7 | POST | `/api/departure-time` | 8절 (예약) | #39 | 후속 확정 |

---

## 2. 역 목록·검색 — `GET /api/stations`

### 2.1 요청

| 파라미터 | 위치 | 필수 | 타입 | 규칙 |
|---|---|---|---|---|
| `q` | query | 필수 | string | 앞뒤 공백을 제거한 뒤 1~20자. 제거 후 빈 값이면 422 (D4) |
| `line` | query | 선택 | string | `"1"`~`"8"` 중 하나. 그 외는 422 |

요청 예시: `GET /api/stations?q=서울&line=1`

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

요청 예시: `GET /api/elevators/status?status=가동%20안됨`

- `status`를 주면 **확인된 설비 중 해당 값과 일치하는 것만** 반환한다. 정보 없음 설비는 어떤 상태 필터에도 포함되지 않는다.
- `status`를 주지 않으면 모든 EV 설비를 반환한다. 정보 없음 설비도 포함한다.

### 5.2 응답 (200)

```json
{
  "elevators": [
    {
      "elevator_id": 102,
      "elevator_name": "승강기)엘리베이터-서울(1)역 섬식(상) 5-1 내부#1",
      "station_id": 1,
      "station_name": "서울역",
      "status": "가동 안됨",
      "data_fresh": true,
      "unknown_reason": null,
      "observed_at": null,
      "fetched_at": "2026-11-13T14:00:00+09:00"
    }
  ],
  "count": 1,
  "limitations": []
}
```

### 5.3 필드

| 필드 | 타입 | 필수 | nullable | 설명 |
|---|---|---|---|---|
| `elevators` | array | 필수 | 아니오 | 설비 목록. 0건이면 `[]` |
| `elevators[].elevator_id` | integer | 필수 | 아니오 | 내부 설비 ID |
| `elevators[].elevator_name` | string | 필수 | 아니오 | 설비명 |
| `elevators[].station_id` | integer | 필수 | 아니오 | 소속 역 ID |
| `elevators[].station_name` | string | 필수 | 아니오 | 소속 역명 |
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

아래 API는 해당 Issue에서 계약을 확정한다. 이 문서는 공통 규칙(0절)만 적용하기로 예약한다.

| API | 확정 Issue | 이미 정해진 공통 규칙 |
|---|---|---|
| `GET /api/stations/{station_id}/alternatives` | #33 | `limitations` 항상 포함. 실제 접근 가능성은 미검증이라고 명시 |
| `GET /api/routes` | #37 | `estimated: true` 포함. `limitations` 포함. 경로 없음 응답은 계약에서 정한다 |
| `POST /api/departure-time` | #39 | `estimated`, `limitations` 포함. 계산 결과를 DB에 저장하지 않음 |
| 실시간 도착 정보 | #40, #41 | 외부 실패 시 "미확인" 표시. 이전 캐시를 현재 정보처럼 보여 주지 않음 |

### 8.1 대체역 `GET /api/stations/{station_id}/alternatives` (#33, 초안 — FE/AI 리뷰 전)

- 응답: `station`, `station_status`(4.4절과 같음), `alternatives[]`, `count`, `search_depth`, `empty_reason`(없으면 `null`), `limitations`.
- `alternatives[]`: `station_id`, `station_name`, `lines`, `distance`(간선 수), `same_line`(대상 역과 노선을 하나라도 공유), `station_status`.
- 후보는 `station_edges`의 ADJACENT 간선을 BFS로 탐색한다(깊이는 `ALTERNATIVE_SEARCH_DEPTH`, 기본 2·임시). 간선이 없으면 후보도 없으며 인접·환승 관계를 추정하지 않는다.
- 가동중 설비가 확인된 역(`AVAILABLE`, `PARTIAL`)만 후보다. 대상 역이 `AVAILABLE`이고 `data_fresh`면 후보 없이 `empty_reason`을 준다.
- 정렬: 같은 노선 먼저, 거리, `AVAILABLE` 먼저, 신선한 데이터 먼저, `station_id`.
- `limitations`에는 항상 "실제 접근 가능한 경로와 환승 가능 여부는 검증되지 않음"이 있다. 오류 형식은 0.6절과 같다.

### 8.2 출발 권장 시각 계산 (#38, #39, 초안 — 계산 로직만 구현, API 엔드포인트는 아직 없음)

- 계산: `권장 출발 = 도착 희망 − (집→역 도보 + 역 진입 + 여유 시간 + 예상 이동 시간 + 출구 이동)`. `app/services/departure_time.py`의 순수 함수이며 DB·네트워크·시스템 시계를 쓰지 않고 결과를 저장하지 않는다.
- 입력: 도착 희망 시각(오프셋 있는 ISO 8601, 초 단위, 타임존 없으면 거부), 예상 이동 시간 1~720분(**외부 입력**, 경로 탐색을 하지 않음), 여유 시간 0~240분, 도보·역 진입·출구 이동 각 0~120분. 기본값은 7·3·3분이며 **검증되지 않은 추정값**이다. `WALK_TO_STATION_MINUTES`, `STATION_ENTRY_MINUTES`, `EXIT_WALK_MINUTES`로 바꾼다.
- 응답 후보 필드: `recommended_departure_at`, `arrival_at`(KST, 0.2절), `total_minutes`, `date_changed`(KST 기준 출발일과 도착일이 다름), `already_past`(현재 시각을 줄 때만 boolean, 아니면 `null`), `estimated`, `timetable_used`, `inputs`, `timeline[]`(`kind`, `label`, `start_at`, `end_at`, `minutes`), `limitations`.
- **실제 열차 시간표를 쓰지 않으므로 항상 `estimated: true`, `timetable_used: false`** 이고 `limitations`에 아래 문구가 항상 들어간다. 탑승 가능한 열차는 고르지 않는다.
  - 실제 열차 시간표를 사용하지 않은 추정 계산이며 탑승 가능한 열차는 확인되지 않음
  - 예상 이동 시간은 외부에서 입력한 값이며 경로 탐색으로 검증되지 않음
  - 도보·역 진입·출구 이동 시간은 추정값이며 개인과 상황에 따라 다를 수 있음
  - (이미 지난 경우) 추천 출발 시각이 현재 시각보다 이미 지났음
- 미구현: 엘리베이터 경고 연동, 시간표 기반 탑승 열차 선택(#35 이후), POST 엔드포인트와 입력 검증 응답(오류는 0.6절 형식).

---

## 9. 실제 데이터에서 확인되지 않은 항목 (미검증)

| 항목 | 현재 상태 |
|---|---|
| 역 마스터(1~8호선 역명·노선·외부 코드) | 저장소에 없다. 공식 출처 확인 후 적재한다(#1, #19). 이 문서의 역·노선 예시는 형식만 보여 준다 |
| 1호선 구간 | 현재 시드에는 1호선 10개 역만 있다. 나머지 구간은 별도 조사 대상이며 미확보다. 1~8호선 전체를 확보했다는 뜻이 아니다 (`data/seed/SOURCES.md`) |
| 환승 관계 | 공식 환승 기록(OA-13290)으로 확인된 34개 그룹만 한 역으로 합친다(서울역은 근거를 검증한 수동 확정). 그 밖의 역은 노선별로 따로 있으며 `is_transfer`는 `false`다. 시드에 없는 1호선 역의 환승은 확인하지 못했다 (`data/seed/SOURCES.md`) |
| 역 좌표 | 출처 미정. `latitude`, `longitude`는 `null`일 수 있다 |
| 설비 노선 소속 | OA-15994에 노선 컬럼이 없다. 응답에 노선 필드를 넣지 않는다 |
| `observed_at` | OA-15994 응답에 원본 측정 시각이 없다. 현재 항상 `null`이다 |
| 갱신 주기 | "매시"는 공공데이터 설명 기준이며 응답으로 확인하지 못했다 |
| TTL | 기본 120분(임시, 설정 가능, 1~1440분). 갱신 주기 미검증 (D1) |
| 엘리베이터 종류 | 응답의 `ELVTR_SE` 중 `EV`만 저장한다. `ES`(에스컬레이터), `WL`(휠체어리프트), `MW`(무빙워크)는 제외한다 |
| 호출 한도 | 미측정 |

---

## 10. 결정 사항과 검토 항목

### 10.1 확정된 결정 (2026-11-13, 사용자 승인)

| # | 항목 | 확정 내용 | 반영 위치 |
|---|---|---|---|
| D1 | TTL | 기본 120분(임시값, 2026-11-14 30분에서 변경), 설정으로 변경 가능. 갱신 주기 확인 후 확정 | 6.1절, 9절 |
| D2 | 일부 설비만 미확인인 역 | 판정 가능한 `status`를 유지하고 `data_fresh: false`, `unknown_elevator_count`, 표준 문구 1을 표시 | 6.2절 |
| D3 | 확인된 설비가 없는 역 | `status: null` (설비가 0개인 역 포함) | 6.2절 |
| D4 | 검색어 `q` | 앞뒤 공백 제거 후 1~20자. 범위 밖이면 422 | 2.1절 |
| D5 | 검색 결과 0건 | 200과 빈 배열 | 0.7절 |
| D6 | 엘리베이터 상태 필터 | `가동중`, `가동 안됨`만 허용. 그 외 값은 422 | 5.1절 |
| D7 | Issue #26의 `UNKNOWN` 역 상태 | `status: null`로 수정. #26 본문을 수정했다 | 6.2절 |
| D8 | 역 좌표 | 없으면 `null` 허용 | 3.3절 |
| D9 | `limitations` 문구 | 한국어 안내 문구 사용. 표준 문구는 0.4절 | 0.4절 |
| D10 | 역 목록 정렬·페이징 | 역명 오름차순, 동일 역명은 `station_id` 오름차순. MVP에서는 페이징 없음 | 2.3절 |
| D11 | 1호선 커버리지 | 1호선은 일부 구간만 확보했으므로 1호선 역이 포함된 응답의 `limitations`에 이를 명시 | 0.4절, 9절 |
| D12 | 역 상세의 0 이하 ID | `station_id`가 0 이하이면 404 `NOT_FOUND`. 정수가 아니면 422 `VALIDATION_ERROR` 유지 | 3.4절 |
| D13 | 좌표 `null` | 좌표가 확인되지 않은 역은 `latitude`, `longitude`를 `null`로 반환하고 임의로 채우지 않음 | 3.2절, 3.3절 |

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
| R9 | AI | 상태를 설명하는 문장을 만들 때 `data_fresh: false`와 `limitations`를 함께 전달하고, 정상 운행으로 요약하지 않을지 |
| R10 | AI | `estimated`와 `limitations` 규칙을 경로·출발 시각 응답(#37, #39)에도 같은 방식으로 쓸지 |
| R11 | BE | #26 구현 시 0절과 6절 규칙을 지키는지 확인 (#26 본문은 #14 규칙에 맞춰 수정함) |

### 10.3 Issue 상태

- **#14:** FE/AI 리뷰 전까지 Open 유지.
- **#26:** 본문을 #14 규칙에 맞췄다. `UNKNOWN` 상태 제거, 가동중·가동 안됨 용어 통일, TTL 임시 기본값 명시. Open.
- **#15:** Open 유지.
