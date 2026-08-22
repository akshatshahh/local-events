from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from the environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    jambase_api_key: str = ""
    jambase_base_url: str = "https://api.data.jambase.com/v3"

    http_timeout_seconds: float = 10.0
    http_max_retries: int = 2
    cache_ttl_seconds: int = 300
    cache_max_entries: int = 512

    # Upper bound on what we will ask any provider for in a single search.
    max_page_size: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
