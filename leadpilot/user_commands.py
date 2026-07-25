from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any

from telegram import BotCommand, BotCommandScopeChat, Update
from telegram.ext import CommandHandler, ContextTypes


ROLE_LABELS = {
    "owner": "Владелец",
    "admin": "Администратор",
    "beta_tester": "Бета-тестер",
    "user": "Пользователь",
}


def _clean_text(value: object, fallback: str = "") -> str:
    text = " ".join(str(value or "").split())
    return text or fallback


def _as_utc(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _load_users(database: Any, owner_user_id: int | None) -> list[dict[str, Any]]:
    where_clause = ""
    parameters: tuple[object, ...] = ()
    if owner_user_id is not None:
        where_clause = "WHERE user_id <> ?"
        parameters = (owner_user_id,)

    statement = database._sql(
        f"""
        SELECT user_id, username, first_name, role, created_at
        FROM user_accounts
        {where_clause}
        ORDER BY created_at DESC, user_id DESC
        """
    )
    with database._connect() as connection:
        accounts = [
            dict(row) for row in connection.execute(statement, parameters).fetchall()
        ]

    for account in accounts:
        account["access"] = database.get_access_state(int(account["user_id"]))
    return accounts


def _tariff_text(account: dict[str, Any]) -> str:
    role = str(account.get("role") or "user")
    if role in {"owner", "admin", "beta_tester"}:
        return f"Безлимитный · {ROLE_LABELS.get(role, role)}"

    access = dict(account.get("access") or {})
    if not access.get("active"):
        return "Нет активного тарифа"

    ends_at = access.get("ends_at")
    end_text = ends_at.strftime("%d.%m.%Y") if hasattr(ends_at, "strftime") else ""
    if access.get("source") == "stars":
        plan_name = _clean_text(access.get("plan_name"), "Оплаченный")
        return f"{plan_name} до {end_text}" if end_text else plan_name
    return f"Пробный до {end_text}" if end_text else "Пробный"


def _user_block(index: int, account: dict[str, Any]) -> str:
    user_id = int(account["user_id"])
    username = _clean_text(account.get("username"))
    first_name = _clean_text(account.get("first_name"), "Без имени")
    created_at = _as_utc(account.get("created_at"))
    created_text = created_at.strftime("%d.%m.%Y %H:%M") if created_at else "неизвестно"

    safe_name = html.escape(first_name)
    safe_username = html.escape(username)
    safe_tariff = html.escape(_tariff_text(account))
    profile = f'<a href="tg://user?id={user_id}">{safe_name}</a>'
    username_text = f"@{safe_username}" if username else "не указан"

    return (
        f"<b>{index}. {profile}</b>\n"
        f"Username: {username_text}\n"
        f"ID: <code>{user_id}</code>\n"
        f"Тариф: {safe_tariff}\n"
        f"Подключился: {created_text} UTC"
    )


def _statistics(accounts: list[dict[str, Any]]) -> tuple[int, int]:
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    today = 0
    week = 0
    for account in accounts:
        created_at = _as_utc(account.get("created_at"))
        if created_at is None:
            continue
        if created_at >= today_start:
            today += 1
        if created_at >= week_start:
            week += 1
    return today, week


def _split_messages(header: str, blocks: Iterable[str], limit: int = 3900) -> list[str]:
    messages: list[str] = []
    current = header
    continuation = "👥 <b>Пользователи бота — продолжение</b>"

    for block in blocks:
        candidate = f"{current}\n\n{block}"
        if len(candidate) <= limit:
            current = candidate
            continue
        messages.append(current)
        current = f"{continuation}\n\n{block}"

    if current:
        messages.append(current)
    return messages


def _merge_commands(
    existing: Iterable[BotCommand],
    additions: dict[str, str],
    *,
    remove: set[str] | None = None,
) -> list[BotCommand]:
    removed = {item.lower().lstrip("/") for item in (remove or set())}
    commands: list[BotCommand] = []
    positions: dict[str, int] = {}

    for command in existing:
        name = command.command.lower().lstrip("/")
        if name in removed:
            continue
        positions[name] = len(commands)
        commands.append(BotCommand(name, command.description))

    for name, description in additions.items():
        normalized = name.lower().lstrip("/")
        item = BotCommand(normalized, description)
        if normalized in positions:
            commands[positions[normalized]] = item
        else:
            positions[normalized] = len(commands)
            commands.append(item)
    return commands


async def _configure_command_menu(application: Any, owner_user_id: int | None) -> None:
    try:
        existing = await application.bot.get_my_commands()
        default_commands = _merge_commands(
            existing,
            {"myid": "Показать ваш Telegram ID"},
            remove={"users"},
        )
        await application.bot.set_my_commands(default_commands)

        if owner_user_id is not None:
            owner_commands = _merge_commands(
                default_commands,
                {"users": "Пользователи, статистика и тарифы"},
            )
            await application.bot.set_my_commands(
                owner_commands,
                scope=BotCommandScopeChat(chat_id=owner_user_id),
            )
    except Exception:
        logging.exception("Failed to update Telegram command menu")


def install_user_commands(bot_class: type[Any]) -> None:
    """Install /myid and owner-only /users into LeadPilotBot."""
    if getattr(bot_class, "_user_commands_installed", False):
        return

    original_build_application = bot_class.build_application

    @wraps(original_build_application)
    def build_application(self: Any):
        application = original_build_application(self)

        async def myid(
            update: Update, context: ContextTypes.DEFAULT_TYPE
        ) -> None:
            del context
            self.ensure_account(update)
            user = update.effective_user
            message = update.effective_message
            if user is None or message is None:
                return
            await message.reply_text(
                f"🆔 Ваш Telegram ID: <code>{user.id}</code>",
                parse_mode="HTML",
            )

        async def users(
            update: Update, context: ContextTypes.DEFAULT_TYPE
        ) -> None:
            del context
            self.ensure_account(update)
            message = update.effective_message
            if message is None:
                return

            if not self.is_owner(update):
                await message.reply_text("⛔ Команда доступна только владельцу.")
                return

            accounts = await asyncio.to_thread(
                _load_users,
                self.db,
                self.settings.owner_telegram_id,
            )
            if not accounts:
                await message.reply_text(
                    "👥 <b>Пользователи бота</b>\n\n"
                    "Кроме владельца пока никто не подключался.",
                    parse_mode="HTML",
                )
                return

            today, week = _statistics(accounts)
            header = (
                "👥 <b>Пользователи бота</b>\n\n"
                f"Всего: <b>{len(accounts)}</b>\n"
                f"Новых сегодня: <b>{today}</b>\n"
                f"Новых за 7 дней: <b>{week}</b>\n\n"
                "Нажмите на имя, чтобы открыть профиль:"
            )
            blocks = [
                _user_block(index, account)
                for index, account in enumerate(accounts, 1)
            ]
            for chunk in _split_messages(header, blocks):
                await message.reply_text(
                    chunk,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )

        application.add_handler(CommandHandler("myid", myid), group=-1)
        application.add_handler(CommandHandler("users", users), group=-1)

        previous_post_init = application.post_init

        async def post_init(app: Any) -> None:
            if previous_post_init is not None:
                await previous_post_init(app)
            await _configure_command_menu(app, self.settings.owner_telegram_id)

        application.post_init = post_init
        return application

    bot_class.build_application = build_application
    bot_class._user_commands_installed = True
