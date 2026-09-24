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

    database_url: str = "sqlite+aiosqlite:///./local-events.db"
    base_url: str = "http://127.0.0.1:8000"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    reservation_cap_per_event: int = 10


def payments_enabled(settings: Settings) -> bool:
    """Turn Checkout on only when both test-mode secrets are present.

    An empty pair leaves the rest of the app running (event search does not
    need Stripe). A live key, or only one of the two secrets, refuses startup.
    """
    key = settings.stripe_secret_key.strip()
    secret = settings.stripe_webhook_secret.strip()
    if not key and not secret:
        return False
    if not key or not secret:
        raise RuntimeError(
            "STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET must both be set "
            "to enable reservations."
        )
    if not key.startswith("sk_test_"):
        raise RuntimeError(
            "STRIPE_SECRET_KEY must start with sk_test_ (test mode only; no live charges)."
        )
    if settings.reservation_cap_per_event < 1:
        raise RuntimeError("RESERVATION_CAP_PER_EVENT must be at least 1.")
    return True


@lru_cache
def get_settings() -> Settings:
    return Settings()
