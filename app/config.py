from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.ttl_limits import TTL_MINUTES_DEFAULT, TTL_MINUTES_MAX, TTL_MINUTES_MIN



class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/dev.db"
    # 빈 값을 허용한다. 키가 없어도 서버와 테스트는 뜬다(외부 호출 기능만 실패).
    seoul_open_api_key: str = ""
    # 환경 변수 ELEVATOR_STATUS_TTL_MINUTES로 바꾼다. 정수 1~1440만 허용한다.
    elevator_status_ttl_minutes: int = Field(
        default=TTL_MINUTES_DEFAULT, ge=TTL_MINUTES_MIN, le=TTL_MINUTES_MAX
    )
    # 환경 변수 ALTERNATIVE_SEARCH_DEPTH로 바꾼다. 대체역 BFS 탐색 깊이(간선 수). 1~10만 허용한다.
    # 기본값 2는 임시 기본값이다(계약 미확정).
    alternative_search_depth: int = Field(default=2, ge=1, le=10)


settings = Settings()
