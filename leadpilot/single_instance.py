from __future__ import annotations

import hashlib
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from .config import Settings
from .database import Database


LOCK_NAMESPACE = b"leadpilot:telegram-poller:v1:"
DEFAULT_RETRY_SECONDS = 10.0


def poller_lock_key(bot_token: str) -> int:
    """Return a stable signed 64-bit PostgreSQL advisory-lock key."""
    digest = hashlib.blake2b(
        LOCK_NAMESPACE + str(bot_token).encode("utf-8"),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


class TelegramPollerLock:
    """Hold a session-level PostgreSQL lock for the lifetime of polling."""

    def __init__(self, database_url: str, bot_token: str) -> None:
        self.database = Database(database_url)
        self.key = poller_lock_key(bot_token)
        self.connection: Any | None = None

    def acquire(self) -> bool:
        # SQLite is used only for local development. A PostgreSQL advisory lock
        # is what coordinates separate Railway services.
        if not self.database.is_postgres:
            return True

        connection = self.database._connect()  # noqa: SLF001 - lock session
        try:
            row = connection.execute(
                "SELECT pg_try_advisory_lock(%s) AS acquired",
                (self.key,),
            ).fetchone()
            acquired = bool(row and row["acquired"])
        except Exception:
            connection.close()
            raise

        if not acquired:
            connection.close()
            return False

        self.connection = connection
        return True

    def release(self) -> None:
        connection = self.connection
        self.connection = None
        if connection is None:
            return
        try:
            connection.execute(
                "SELECT pg_advisory_unlock(%s)",
                (self.key,),
            )
        except Exception:
            logging.exception("Failed to release Telegram poller lock")
        finally:
            connection.close()


def run_single_telegram_poller(
    run_bot: Callable[[], None],
    *,
    settings_factory: Callable[[], Settings] = Settings.from_env,
    retry_seconds: float = DEFAULT_RETRY_SECONDS,
) -> None:
    """Run polling only after this process owns the shared database lock."""
    settings = settings_factory()
    lock = TelegramPollerLock(
        settings.database_url,
        settings.telegram_bot_token,
    )
    service_name = os.getenv("RAILWAY_SERVICE_NAME", "local").strip() or "local"

    while True:
        try:
            if lock.acquire():
                break
        except Exception:
            logging.exception(
                "Could not acquire Telegram poller lock; retrying in %.0f seconds",
                retry_seconds,
            )
        else:
            logging.warning(
                "Telegram polling is already owned by another process. "
                "Service %s is waiting and will not consume updates.",
                service_name,
            )
        time.sleep(max(float(retry_seconds), 1.0))

    logging.info(
        "Telegram polling lock acquired by Railway service %s",
        service_name,
    )
    try:
        run_bot()
    finally:
        lock.release()
