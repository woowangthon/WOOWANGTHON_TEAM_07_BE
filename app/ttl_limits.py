"""엘리베이터 상태 TTL의 허용 범위와 기본값. 설정을 읽지 않는 순수 상수 모듈이다.

기본값 30분은 임시 기본값이다(D1, 갱신 주기 미검증). 설정 키는 ELEVATOR_STATUS_TTL_MINUTES다.
"""

TTL_MINUTES_MIN = 1
TTL_MINUTES_MAX = 1440
TTL_MINUTES_DEFAULT = 30
