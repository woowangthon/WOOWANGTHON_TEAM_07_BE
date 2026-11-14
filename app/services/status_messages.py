"""docs/api-contract.md 0.4절의 표준 limitations 문구 (D9, D11).

문구를 바꾸면 계약 문서와 함께 바꾼다. FE는 받은 문구를 그대로 표시한다.
임의로 새 문구를 만들지 않는다.
"""

PARTIAL_UNKNOWN = "일부 설비 상태가 확인되지 않아 역 전체 상태를 단정할 수 없음"
GLOBAL_SOME_UNKNOWN = "일부 설비의 상태 정보가 없거나 오래되어 현재 상태를 확인하지 못함"
NO_CONFIRMED = "확인 가능한 엘리베이터 상태가 없어 역 상태를 판정할 수 없음"
NO_ELEVATORS = "엘리베이터 정보가 없어 역 상태를 판정할 수 없음"
LINE_1_PARTIAL = "1호선은 일부 구간의 역만 포함되어 있어 전체 구간을 포함하지 않음"

# 대체역 API(#33) 문구. 계약 8.1절(초안).
ALT_UNVERIFIED = "대체역은 역 간 연결 데이터 기준의 추천이며 실제 접근 가능한 경로와 환승 가능 여부는 검증되지 않음"
ALT_NO_EDGES = "해당 역의 인접역 연결 데이터가 없어 대체역을 찾을 수 없음"
ALT_TARGET_OK_REASON = "대상 역의 엘리베이터가 확인된 기준으로 모두 가동중이어서 대체역이 필요하지 않음"
ALT_NO_CANDIDATE_REASON = "탐색 범위 안에 가동중인 엘리베이터가 확인된 인접역이 없음"

# 출발 권장 시각 계산 문구(#39). 계약 8.2절(초안).
DEP_NO_TIMETABLE = "실제 열차 시간표를 사용하지 않은 추정 계산이며 탑승 가능한 열차는 확인되지 않음"
DEP_TRAVEL_EXTERNAL = "예상 이동 시간은 외부에서 입력한 값이며 경로 탐색으로 검증되지 않음"
DEP_SEGMENT_ESTIMATES = "도보·역 진입·출구 이동 시간은 추정값이며 개인과 상황에 따라 다를 수 있음"
DEP_ALREADY_PAST = "추천 출발 시각이 현재 시각보다 이미 지났음"
DEP_ELEVATOR_UNCONFIRMED = "역·설비 상태 정보가 입력되지 않아 엘리베이터 경고를 확인하지 못함"

# 실시간 도착 정보 문구(#40, #41). 계약 8.3절(초안).
ARR_NOT_CONFIGURED = "실시간 도착 정보 출처가 연동되지 않아 도착 시각을 확인하지 못함"
ARR_PROVIDER_FAILED = "실시간 도착 정보를 가져오지 못해 도착 시각을 확인하지 못함"
ARR_EMPTY = "조회 시점에 도착 예정 열차 정보가 없음(운행 종료인지 정보 미제공인지는 구분되지 않음)"
ARR_SNAPSHOT = "조회 시점 기준의 도착 정보이며 이후 달라질 수 있음"

# 경로 API(#37)와 엘리베이터 경고 문구. 계약 8.4절.
ROUTE_ESTIMATED = "소요 시간은 역 간 이동과 환승에 고정 시간을 가정한 추정값이며 실제 열차 시간표와 다를 수 있음"
ROUTE_EDGE_TIMETABLE = "역 간 연결은 열차 시간표에서 추출한 것이며 공식 인접역 자료로 검증되지 않음"
ROUTE_EDGE_PROVISIONAL = "일부 역 간 연결은 공식 근거가 없는 임시 데이터임"
ROUTE_ACCESSIBILITY_UNVERIFIED = "엘리베이터만으로 이동할 수 있는 환승·출입 동선인지는 검증되지 않음"
ROUTE_NOT_FOUND = "두 역을 잇는 경로를 찾을 수 없습니다."
ROUTE_SAME_STATION = "출발역과 도착역이 같습니다."

WARN_WARNING = "경로 위 일부 역에서 엘리베이터가 가동 안됨이거나 일부만 가동중임"
WARN_UNCONFIRMED = "경로 위 일부 역의 엘리베이터 상태를 확인하지 못해 경로 전체를 단정할 수 없음"
WARN_CLEAR = "확인된 설비 기준으로 경로 위 모든 역의 엘리베이터가 가동중임"
WARN_ITEM_UNAVAILABLE = "확인된 엘리베이터가 모두 가동 안됨"
WARN_ITEM_PARTIAL = "일부 엘리베이터가 가동 안됨"
WARN_ITEM_UNCONFIRMED = "엘리베이터 상태를 확인하지 못했거나 일부만 확인됨"
WARN_NO_ROUTE_STATIONS = "경로를 찾지 못해 엘리베이터 경고를 확인하지 못함"
