from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/dev.db"
    # 빈 값을 허용한다. 키가 없어도 서버와 테스트는 뜬다(외부 호출 기능만 실패).
    seoul_open_api_key: str = ""


settings = Settings()
