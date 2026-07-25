from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
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
        ORDER BY created_at ASC, user_id ASC
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
    username = _clean_text(account.get("username"))
    first_name = _clean_text(account.get("first_name"), "Без имени")
    username_text = f"@{username}" if username else "не указан"
    return (
        f"{index}. {first_name}\n"
        f"Telegram: {username_text}\n"
        f"ID: {int(account['user_id'])}\n"
        f"Тариф: {_tariff_text(account)}"
    )


def _split_messages(header: str, blocks: Iterable[str], limit: int = 3900) -> list[str]:
    messages: list[str] = []
    current = header
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            messages.append(current)
        current = block
    if current:
        messages.append(current)
    return messages


def _merge_commands(
    existing: Iterable[BotCommand], additions: dict[str, str]
) -> list[tuple[str, str]]:
    commands: list[tuple[str, str]] = []
    positions: dict[str, int] = {}
    for command in existing:
        name = command.command.lower()
        positions[name] = len(commands)
        commands.append((name, command.description))
    for name, description in additions.items():
        normalized = name.lower().lstrip("/")
        if normalized in positions:
            commands[positions[normalized]] = (normalized, description)
        else:
            positions[normalized] = len(commands)
            commands.append((normalized, description))
    return commands


async def _configure_command_menu(application: Any, owner_user_id: int | None) -> None:
    try:
        existing = await application.bot.get_my_commands()
        default_commands = _merge_commands(
            existing,
            {"myid": "Показать ваш Telegram ID"},
        )
        await application.bot.set_my_commands(default_commands)
        if owner_user_id is not None:
            owner_commands = _merge_commands(
                [BotCommand(command, description) for command, description in default_commands],
                {"users": "Показать пользователей и тарифы"},
            )
            await application.bot.set_my_commands(
                owner_commands,
                scope=BotCommandScopeChat(chat_id=owner_user_id),
            )
    except Exception:
        logging.exception("Failed to update Telegram command menu")


def install_user_commands(bot_class: type[Any]) -> None:
    """Add /myid and owner-only /users to LeadPilotBot."""
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
            if not user or not message:
                return
            await message.reply_text(f"🆔 Ваш Telegram ID: {user.id}")

        async def users(
            update: Update, context: ContextTypes.DEFAULT_TYPE
        ) -> None:
            del context
            self.ensure_account(update)
            message = update.effective_message
            if not message:
                return
            if not self.is_owner(update):
                await message.reply_text("Команда доступна только владельцу.")
                return

            accounts = await asyncio.to_thread(
                _load_users,
                self.db,
                self.settings.owner_telegram_id,
            )
            if not accounts:
                await message.reply_text(
                    "👥 Пользователи бота\n\nКроме владельца пока никто не подключался."
                )
                return

            blocks = [
                _user_block(index, account)
                for index, account in enumerate(accounts, 1)
            ]
            header = f"👥 Пользователи бота: {len(accounts)}"
            for chunk in _split_messages(header, blocks):
                await message.reply_text(chunk, disable_web_page_preview=True)

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
