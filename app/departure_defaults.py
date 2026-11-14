"""출발 권장 시각 계산의 기본값과 허용 범위. 설정을 읽지 않는 순수 상수 모듈이다.

도보·역 진입·출구 이동 시간은 검증되지 않은 추정 기본값이다(이슈 #38). 설정으로 바꾼다.
- 집 -> 역 도보: WALK_TO_STATION_MINUTES (기본 7분)
- 역 진입(개찰구·승강기 등): STATION_ENTRY_MINUTES (기본 3분)
- 하차 후 출구 이동: EXIT_WALK_MINUTES (기본 3분)
"""

WALK_TO_STATION_DEFAULT = 7
STATION_ENTRY_DEFAULT = 3
EXIT_WALK_DEFAULT = 3
SEGMENT_MINUTES_MIN = 0
SEGMENT_MINUTES_MAX = 120

TRAVEL_MINUTES_MIN = 1
TRAVEL_MINUTES_MAX = 720  # 12시간
BUFFER_MINUTES_MIN = 0
BUFFER_MINUTES_MAX = 240  # 4시간
