from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    duffel_api_key: str
    duffel_api_url: str = "https://api.duffel.com"
    duffel_api_version: str = "v2"
    redis_url: str = "redis://localhost:6379"
    duffel_daily_search_budget: int = 2000
    duffel_monthly_search_budget: int = 40000


settings = Settings()
