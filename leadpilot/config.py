from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    serpapi_key: str
    openai_api_key: str
    database_url: str
    owner_telegram_id: int | None
    demo_mode: bool
    search_provider: str
    openai_model: str
    support_username: str

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("Не задана переменная TELEGRAM_BOT_TOKEN")

        demo_mode = _as_bool(os.getenv("DEMO_MODE"), default=False)
        search_provider = os.getenv("SEARCH_PROVIDER", "serpapi").strip().lower()
        if search_provider != "serpapi":
            raise RuntimeError("SEARCH_PROVIDER должен иметь значение serpapi")

        serpapi_key = os.getenv("SERPAPI_KEY", "").strip()
        if not demo_mode and not serpapi_key:
            raise RuntimeError("Не задана переменная SERPAPI_KEY")

        owner_raw = os.getenv("OWNER_TELEGRAM_ID", "").strip()
        try:
            owner_id = int(owner_raw) if owner_raw else None
        except ValueError as exc:
            raise RuntimeError("OWNER_TELEGRAM_ID должен быть целым числом") from exc

        return cls(
            telegram_bot_token=token,
            serpapi_key=serpapi_key,
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            database_url=os.getenv(
                "DATABASE_URL", "sqlite:///./leadpilot.db"
            ).strip(),
            owner_telegram_id=owner_id,
            demo_mode=demo_mode,
            search_provider=search_provider,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna").strip(),
            support_username=os.getenv(
                "SUPPORT_TELEGRAM", "@DigitalWave_vl"
            ).strip(),
        )
