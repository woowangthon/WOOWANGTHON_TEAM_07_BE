from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.departure_defaults import (
    EXIT_WALK_DEFAULT,
    SEGMENT_MINUTES_MAX,
    SEGMENT_MINUTES_MIN,
    STATION_ENTRY_DEFAULT,
    WALK_TO_STATION_DEFAULT,
)
from app.ttl_limits import TTL_MINUTES_DEFAULT, TTL_MINUTES_MAX, TTL_MINUTES_MIN



class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/dev.db"
    # 빈 값을 허용한다. 키가 없어도 서버와 테스트는 뜬다(외부 호출 기능만 실패).
    seoul_open_api_key: str = ""
    # 환경 변수 ELEVATOR_STATUS_TTL_MINUTES로 바꾼다. 정수 1~1440만 허용한다.
    # 기본 120분(2시간)은 갱신 주기 미검증에 따른 임시 기본값이다.
    elevator_status_ttl_minutes: int = Field(
        default=TTL_MINUTES_DEFAULT, ge=TTL_MINUTES_MIN, le=TTL_MINUTES_MAX
    )
    # 환경 변수 ALTERNATIVE_SEARCH_DEPTH로 바꾼다. 대체역 BFS 탐색 깊이(간선 수). 1~10만 허용한다.
    # 기본값 2는 임시 기본값이다(계약 미확정).
    alternative_search_depth: int = Field(default=2, ge=1, le=10)

    # 출발 권장 시각 계산의 추정 기본값(분, 0~120). 환경 변수로 바꾼다. 검증되지 않은 추정값이다.
    walk_to_station_minutes: int = Field(
        default=WALK_TO_STATION_DEFAULT, ge=SEGMENT_MINUTES_MIN, le=SEGMENT_MINUTES_MAX
    )
    station_entry_minutes: int = Field(
        default=STATION_ENTRY_DEFAULT, ge=SEGMENT_MINUTES_MIN, le=SEGMENT_MINUTES_MAX
    )
    exit_walk_minutes: int = Field(
        default=EXIT_WALK_DEFAULT, ge=SEGMENT_MINUTES_MIN, le=SEGMENT_MINUTES_MAX
    )


settings = Settings()
