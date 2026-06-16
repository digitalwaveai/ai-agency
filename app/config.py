from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    search_provider: str = "demo"
    serpapi_key: str | None = None
    brave_search_api_key: str | None = None
    bing_search_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    database_url: str = "sqlite:///./leads.db"
    refresh_interval_hours: int = 24
    demo_mode: bool = True
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache
def get_settings() -> Settings:
    return Settings()
