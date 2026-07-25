from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from .bot import MENU

BETA_DURATION = timedelta(days=7)


def _now_utc_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _create_beta_schema(database: Any) -> None:
    statement = """
        CREATE TABLE IF NOT EXISTS beta_access_periods (
            user_id BIGINT PRIMARY KEY,
            starts_at TIMESTAMP NOT NULL,
            ends_at TIMESTAMP NOT NULL
        )
    """
    with database._connect() as connection:
        connection.execute(statement)
        connection.commit()


def _grant_beta_period(database: Any, user_id: int) -> datetime:
    starts_at = _now_utc_naive()
    ends_at = starts_at + BETA_DURATION
    statement = database._sql(
        """
        INSERT INTO beta_access_periods (user_id, starts_at, ends_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            starts_at = excluded.starts_at,
            ends_at = excluded.ends_at
        """
    )
    with database._connect() as connection:
        connection.execute(
            statement,
            (
                user_id,
                database._db_datetime(starts_at),
                database._db_datetime(ends_at),
            ),
        )
        connection.commit()
    return ends_at


def _delete_beta_period(database: Any, user_id: int) -> None:
    statement = database._sql(
        "DELETE FROM beta_access_periods WHERE user_id = ?"
    )
    with database._connect() as connection:
        connection.execute(statement, (user_id,))
        connection.commit()


def _get_beta_expiry(database: Any, user_id: int) -> datetime | None:
    statement = database._sql(
        "SELECT ends_at FROM beta_access_periods WHERE user_id = ?"
    )
    with database._connect() as connection:
        row = connection.execute(statement, (user_id,)).fetchone()
    return database._as_datetime(row["ends_at"]) if row else None


def _migrate_existing_beta_testers(database: Any) -> None:
    select_statement = """
        SELECT user_id, updated_at
        FROM user_accounts
        WHERE role = 'beta_tester'
    """
    insert_statement = database._sql(
        """
        INSERT INTO beta_access_periods (user_id, starts_at, ends_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO NOTHING
        """
    )
    cleanup_statement = """
        DELETE FROM beta_access_periods
        WHERE user_id NOT IN (
            SELECT user_id
            FROM user_accounts
            WHERE role = 'beta_tester'
        )
    """
    with database._connect() as connection:
        rows = connection.execute(select_statement).fetchall()
        for row in rows:
            starts_at = database._as_datetime(row["updated_at"])
            ends_at = starts_at + BETA_DURATION
            connection.execute(
                insert_statement,
                (
                    int(row["user_id"]),
                    database._db_datetime(starts_at),
                    database._db_datetime(ends_at),
                ),
            )
        connection.execute(cleanup_statement)
        connection.commit()


def install_beta_access(bot_class: type[Any], database_class: type[Any]) -> None:
    """Give beta testers unlimited access for exactly seven days."""
    if getattr(database_class, "_beta_access_installed", False):
        return

    original_init_schema = database_class.init_schema
    original_set_role = database_class.set_role
    original_get_role = database_class.get_role
    original_get_role_record = database_class.get_role_record
    original_ensure_owner = database_class.ensure_owner

    @wraps(original_init_schema)
    def init_schema(self: Any) -> None:
        original_init_schema(self)
        _create_beta_schema(self)
        _migrate_existing_beta_testers(self)

    @wraps(original_set_role)
    def set_role(
        self: Any,
        user_id: int,
        role: str,
        *,
        managed_by: int | None = None,
    ) -> bool:
        changed = original_set_role(
            self,
            user_id,
            role,
            managed_by=managed_by,
        )
        if not changed:
            return False

        if role == "beta_tester":
            _grant_beta_period(self, user_id)
        else:
            _delete_beta_period(self, user_id)
        return True

    def get_beta_expires_at(self: Any, user_id: int) -> datetime | None:
        return _get_beta_expiry(self, user_id)

    @wraps(original_get_role)
    def get_role(self: Any, user_id: int) -> str:
        role = original_get_role(self, user_id)
        if role != "beta_tester":
            return role

        expires_at = _get_beta_expiry(self, user_id)
        if expires_at is None:
            _grant_beta_period(self, user_id)
            return "beta_tester"

        if _now_utc_naive() < expires_at:
            return "beta_tester"

        original_set_role(self, user_id, "user")
        _delete_beta_period(self, user_id)
        return "user"

    @wraps(original_get_role_record)
    def get_role_record(self: Any, user_id: int) -> dict[str, Any]:
        record = original_get_role_record(self, user_id)
        if record.get("role") == "beta_tester":
            record["role"] = get_role(self, user_id)
        record["beta_expires_at"] = (
            _get_beta_expiry(self, user_id)
            if record.get("role") == "beta_tester"
            else None
        )
        if record.get("role") != "beta_tester":
            record["managed_by"] = None
        return record

    @wraps(original_ensure_owner)
    def ensure_owner(self: Any, owner_user_id: int) -> None:
        original_ensure_owner(self, owner_user_id)
        _delete_beta_period(self, owner_user_id)

    database_class.init_schema = init_schema
    database_class.set_role = set_role
    database_class.get_role = get_role
    database_class.get_role_record = get_role_record
    database_class.get_beta_expires_at = get_beta_expires_at
    database_class.ensure_owner = ensure_owner
    database_class._beta_access_installed = True

    original_start = bot_class.start
    original_show_role = bot_class.show_role

    @wraps(original_start)
    async def start(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        self.ensure_account(update)
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return

        if self.role(update) != "beta_tester":
            await original_start(self, update, context)
            return

        expires_at = self.db.get_beta_expires_at(user.id)
        expiry_text = (
            expires_at.strftime("%d.%m.%Y в %H:%M UTC")
            if expires_at is not None
            else "через 7 дней"
        )
        await message.reply_text(
            "✨ LeadPilot AI\n\n"
            "AI-система поиска клиентов для специалистов и агентств.\n\n"
            "👤 Ваш аккаунт\n\n"
            "Роль: Бета-тестер\n"
            "Доступ: без тарифных лимитов\n"
            f"Срок: до {expiry_text}\n"
            "Управление пользователями: недоступно\n\n"
            "Выберите действие:",
            reply_markup=MENU,
        )

    @wraps(original_show_role)
    async def show_role(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        self.ensure_account(update)
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return

        if self.role(update) != "beta_tester":
            await original_show_role(self, update, context)
            return

        expires_at = self.db.get_beta_expires_at(user.id)
        expiry_text = (
            expires_at.strftime("%d.%m.%Y в %H:%M UTC")
            if expires_at is not None
            else "через 7 дней"
        )
        await message.reply_text(
            "👤 Роль аккаунта\n\n"
            "Роль: Бета-тестер\n"
            "Доступ: полный безлимит\n"
            f"Действует до: {expiry_text}\n"
            "Управление пользователями: недоступно",
            reply_markup=MENU,
        )

    bot_class.start = start
    bot_class.show_role = show_role
    bot_class._beta_access_installed = True

    from . import user_commands

    original_load_users = user_commands._load_users
    original_tariff_text = user_commands._tariff_text

    @wraps(original_load_users)
    def load_users(
        database: Any,
        owner_user_id: int | None,
    ) -> list[dict[str, Any]]:
        accounts = original_load_users(database, owner_user_id)
        for account in accounts:
            user_id = int(account["user_id"])
            actual_role = database.get_role(user_id)
            account["role"] = actual_role
            account["beta_expires_at"] = (
                database.get_beta_expires_at(user_id)
                if actual_role == "beta_tester"
                else None
            )
        return accounts

    @wraps(original_tariff_text)
    def tariff_text(account: dict[str, Any]) -> str:
        if str(account.get("role") or "") == "beta_tester":
            expires_at = account.get("beta_expires_at")
            if hasattr(expires_at, "strftime"):
                return (
                    "Бета-тестер · безлимит до "
                    f"{expires_at.strftime('%d.%m.%Y %H:%M UTC')}"
                )
            return "Бета-тестер · безлимит на 7 дней"
        return original_tariff_text(account)

    user_commands._load_users = load_users
    user_commands._tariff_text = tariff_text
