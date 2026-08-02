from __future__ import annotations

import asyncio
import hashlib
import logging
from functools import wraps
from typing import Any


BROADCAST_ID = "lead-actions-service-restored-2026-08-02-v1"
BROADCAST_TEXT = (
    "✅ Работа восстановлена\n\n"
    "Все три функции снова доступны:\n"
    "• «📋 Мои лиды»\n"
    "• «💎 Анализ клиента»\n"
    "• «✉️ Создать сообщение»\n\n"
    "Бот снова работает в обычном режиме. Спасибо за ожидание и понимание!"
)


def _ensure_schema(database: Any) -> None:
    with database._connect() as connection:  # noqa: SLF001
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS system_broadcasts (
                broadcast_id TEXT PRIMARY KEY,
                completed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS system_broadcast_deliveries (
                broadcast_id TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                status TEXT NOT NULL DEFAULT 'claimed',
                error_text TEXT NOT NULL DEFAULT '',
                attempted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (broadcast_id, user_id)
            )
            """
        )
        connection.commit()


def _is_completed(database: Any, broadcast_id: str) -> bool:
    statement = database._sql(
        "SELECT broadcast_id FROM system_broadcasts WHERE broadcast_id = ?"
    )
    with database._connect() as connection:  # noqa: SLF001
        return connection.execute(statement, (broadcast_id,)).fetchone() is not None


def _recipient_ids(database: Any) -> list[int]:
    with database._connect() as connection:  # noqa: SLF001
        rows = connection.execute(
            "SELECT user_id FROM user_accounts ORDER BY user_id"
        ).fetchall()
    return [int(row["user_id"]) for row in rows]


def _claim_delivery(database: Any, broadcast_id: str, user_id: int) -> bool:
    statement = database._sql(
        """
        INSERT INTO system_broadcast_deliveries (broadcast_id, user_id)
        VALUES (?, ?)
        ON CONFLICT(broadcast_id, user_id) DO NOTHING
        """
    )
    with database._connect() as connection:  # noqa: SLF001
        cursor = connection.execute(statement, (broadcast_id, user_id))
        connection.commit()
        return int(cursor.rowcount or 0) == 1


def _finish_delivery(
    database: Any,
    broadcast_id: str,
    user_id: int,
    *,
    status: str,
    error_text: str = "",
) -> None:
    statement = database._sql(
        """
        UPDATE system_broadcast_deliveries
        SET status = ?, error_text = ?, attempted_at = CURRENT_TIMESTAMP
        WHERE broadcast_id = ? AND user_id = ?
        """
    )
    with database._connect() as connection:  # noqa: SLF001
        connection.execute(
            statement,
            (status, str(error_text or "")[:1000], broadcast_id, user_id),
        )
        connection.commit()


def _mark_completed(database: Any, broadcast_id: str) -> None:
    statement = database._sql(
        """
        INSERT INTO system_broadcasts (broadcast_id)
        VALUES (?)
        ON CONFLICT(broadcast_id) DO NOTHING
        """
    )
    with database._connect() as connection:  # noqa: SLF001
        connection.execute(statement, (broadcast_id,))
        connection.commit()


def _lock_key(broadcast_id: str) -> int:
    digest = hashlib.blake2b(
        f"leadpilot:broadcast:{broadcast_id}".encode("utf-8"),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


def _try_broadcast_lock(database: Any, broadcast_id: str) -> Any | None:
    if not database.is_postgres:
        return True
    connection = database._connect()  # noqa: SLF001
    row = connection.execute(
        "SELECT pg_try_advisory_lock(%s) AS acquired",
        (_lock_key(broadcast_id),),
    ).fetchone()
    if row and bool(row["acquired"]):
        return connection
    connection.close()
    return None


def _release_broadcast_lock(database: Any, lock: Any, broadcast_id: str) -> None:
    if not database.is_postgres or lock is True or lock is None:
        return
    try:
        lock.execute(
            "SELECT pg_advisory_unlock(%s)",
            (_lock_key(broadcast_id),),
        )
    finally:
        lock.close()


async def send_one_time_notice(application: Any, database: Any) -> None:
    """Send the current service notice to every existing account at most once."""
    lock = _try_broadcast_lock(database, BROADCAST_ID)
    if lock is None:
        logging.info("One-time notice is being handled by another process")
        return

    try:
        _ensure_schema(database)
        if _is_completed(database, BROADCAST_ID):
            return

        recipients = _recipient_ids(database)
        sent = 0
        failed = 0
        for user_id in recipients:
            if not _claim_delivery(database, BROADCAST_ID, user_id):
                continue
            try:
                await application.bot.send_message(
                    chat_id=user_id,
                    text=BROADCAST_TEXT,
                )
            except Exception as exc:
                failed += 1
                _finish_delivery(
                    database,
                    BROADCAST_ID,
                    user_id,
                    status="failed",
                    error_text=str(exc),
                )
                logging.warning(
                    "One-time notice could not be delivered to user %s: %s",
                    user_id,
                    exc,
                )
            else:
                sent += 1
                _finish_delivery(
                    database,
                    BROADCAST_ID,
                    user_id,
                    status="sent",
                )
            await asyncio.sleep(0.05)

        _mark_completed(database, BROADCAST_ID)
        logging.info(
            "One-time service notice completed: sent=%s failed=%s recipients=%s",
            sent,
            failed,
            len(recipients),
        )
    finally:
        _release_broadcast_lock(database, lock, BROADCAST_ID)


def install_one_time_service_notice(bot_class: type[Any]) -> None:
    """Attach the one-time notice to application startup only."""
    if getattr(bot_class, "_one_time_service_notice_installed", False):
        return

    original_build_application = bot_class.build_application

    @wraps(original_build_application)
    def build_application(self: Any):
        application = original_build_application(self)
        previous_post_init = application.post_init

        async def post_init(app: Any) -> None:
            if previous_post_init is not None:
                await previous_post_init(app)
            await send_one_time_notice(app, self.db)

        application.post_init = post_init
        return application

    bot_class.build_application = build_application
    bot_class._one_time_service_notice_installed = True
