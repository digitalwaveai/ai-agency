from __future__ import annotations

import logging
from functools import wraps
from typing import Any

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    MenuButtonCommands,
    Update,
)
from telegram.ext import ContextTypes


DEFAULT_COMMANDS = (
    BotCommand("start", "Запустить бота"),
    BotCommand("menu", "Открыть главное меню"),
    BotCommand("new_project", "Создать новый проект"),
    BotCommand("projects", "Мои проекты"),
    BotCommand("find", "Найти клиентов"),
    BotCommand("leads", "Мои лиды"),
    BotCommand("analyze", "Анализ клиента"),
    BotCommand("message", "Создать сообщение"),
    BotCommand("radars", "Создать радар"),
    BotCommand("radar_run", "Запустить радар по ID"),
    BotCommand("export", "Экспортировать лиды"),
    BotCommand("analytics", "Аналитика лидов"),
    BotCommand("plans", "Тарифы"),
    BotCommand("limits", "Мои лимиты"),
    BotCommand("support", "Поддержка"),
    BotCommand("role", "Моя роль"),
    BotCommand("status", "Проверить работу бота"),
    BotCommand("myid", "Показать Telegram ID"),
    BotCommand("help", "Справка по командам"),
    BotCommand("cancel", "Отменить текущий шаг"),
)

# Владелец видит те же основные команды и только одну дополнительную — /users.
# Команды назначения ролей и переключения цен остаются рабочими вручную,
# но намеренно не показываются в меню Telegram.
OWNER_COMMANDS = DEFAULT_COMMANDS + (
    BotCommand("users", "Пользователи, тарифы и лимиты"),
)


async def register_command_menu(
    application: Any,
    owner_user_id: int | None,
) -> None:
    """Restore the basic Telegram command list and its menu button."""
    await application.bot.set_my_commands(DEFAULT_COMMANDS)
    await application.bot.set_chat_menu_button(
        menu_button=MenuButtonCommands(),
    )

    if owner_user_id is not None:
        await application.bot.set_my_commands(
            OWNER_COMMANDS,
            scope=BotCommandScopeChat(chat_id=owner_user_id),
        )
        await application.bot.set_chat_menu_button(
            chat_id=owner_user_id,
            menu_button=MenuButtonCommands(),
        )


async def ensure_chat_menu_button(update: Update, context: Any) -> None:
    """Refresh the commands menu for the current private chat."""
    chat = update.effective_chat
    if chat is None:
        return
    try:
        await context.bot.set_chat_menu_button(
            chat_id=chat.id,
            menu_button=MenuButtonCommands(),
        )
    except Exception:
        logging.exception("Failed to restore Telegram command menu button")


def install_telegram_command_menu(bot_class: type[Any]) -> None:
    """Install the basic slash-command menu without changing reply buttons."""
    if getattr(bot_class, "_telegram_command_menu_installed", False):
        return

    original_build_application = bot_class.build_application
    original_start = bot_class.start
    original_menu = bot_class.menu

    @wraps(original_start)
    async def start(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> Any:
        result = await original_start(self, update, context)
        await ensure_chat_menu_button(update, context)
        return result

    @wraps(original_menu)
    async def menu(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> Any:
        result = await original_menu(self, update, context)
        await ensure_chat_menu_button(update, context)
        return result

    @wraps(original_build_application)
    def build_application(self: Any):
        application = original_build_application(self)
        previous_post_init = application.post_init

        async def post_init(app: Any) -> None:
            if previous_post_init is not None:
                await previous_post_init(app)
            try:
                await register_command_menu(
                    app,
                    self.settings.owner_telegram_id,
                )
            except Exception:
                logging.exception("Failed to restore Telegram command menu")

        application.post_init = post_init
        return application

    bot_class.start = start
    bot_class.menu = menu
    bot_class.build_application = build_application
    bot_class._telegram_command_menu_installed = True
