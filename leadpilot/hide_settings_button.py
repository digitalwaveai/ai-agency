from __future__ import annotations

import re
import sys
from functools import wraps
from typing import Any

from telegram import BotCommand, MenuButtonCommands, ReplyKeyboardMarkup
from telegram.ext import filters

from . import bot as bot_module


TELEGRAM_COMMAND_MENU = (
    BotCommand("leads", "Мои лиды"),
    BotCommand("analyze", "Анализ клиента"),
    BotCommand("message", "Создать сообщение"),
)


def visible_menu_rows() -> list[list[str]]:
    """Build the regular reply keyboard with the original visible labels."""
    return [
        [bot_module.BUTTON_NEW_PROJECT, bot_module.BUTTON_PROJECTS],
        [bot_module.BUTTON_SEARCH, bot_module.BUTTON_LEADS],
        [bot_module.BUTTON_PIPELINE, bot_module.BUTTON_EXPORT],
        [bot_module.BUTTON_ANALYTICS],
        [bot_module.BUTTON_ANALYZE, bot_module.BUTTON_MESSAGE],
        [bot_module.BUTTON_RADARS, bot_module.BUTTON_LIMITS],
        [bot_module.BUTTON_PLANS],
        [bot_module.BUTTON_SUPPORT],
    ]


def install_hide_settings_button(bot_class: type[Any]) -> None:
    """Keep the normal keyboard and expose lead actions in Telegram's menu."""
    if getattr(bot_class, "_settings_button_hidden", False):
        return

    old_menu = bot_module.MENU
    visible_buttons = tuple(
        button
        for button in bot_module.MENU_BUTTONS
        if button != bot_module.BUTTON_SETTINGS
    )
    new_pattern = rf"^(?:{'|'.join(re.escape(item) for item in visible_buttons)})$"
    new_menu = ReplyKeyboardMarkup(
        visible_menu_rows(),
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )

    bot_module.MENU_BUTTONS = visible_buttons
    bot_module.MENU_BUTTON_PATTERN = new_pattern
    bot_module.USER_INPUT_FILTER = (
        filters.TEXT & ~filters.COMMAND & ~filters.Regex(new_pattern)
    )
    bot_module.MENU = new_menu

    # Other feature modules import MENU by value. Replace those references too,
    # so every future message shows the same keyboard without the settings button.
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith("leadpilot") or module is None:
            continue
        if getattr(module, "MENU", None) is old_menu:
            setattr(module, "MENU", new_menu)

    original_build_application = bot_class.build_application

    @wraps(original_build_application)
    def build_application(self: Any):
        application = original_build_application(self)
        previous_post_init = application.post_init

        async def post_init(app: Any) -> None:
            if previous_post_init is not None:
                await previous_post_init(app)
            await app.bot.set_my_commands(TELEGRAM_COMMAND_MENU)
            await app.bot.set_chat_menu_button(
                menu_button=MenuButtonCommands()
            )

        application.post_init = post_init
        return application

    bot_class.build_application = build_application
    bot_class._settings_button_hidden = True
