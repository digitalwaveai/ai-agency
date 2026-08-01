from __future__ import annotations

import re
import sys
from typing import Any

from telegram import ReplyKeyboardMarkup
from telegram.ext import filters

from . import bot as bot_module


LEADS_COMMAND_BUTTON = "/leads"
ANALYZE_COMMAND_BUTTON = "/analyze"
MESSAGE_COMMAND_BUTTON = "/message"


def visible_menu_rows() -> list[list[str]]:
    """Build the menu; three lead actions use their working slash commands."""
    return [
        [bot_module.BUTTON_NEW_PROJECT, bot_module.BUTTON_PROJECTS],
        [bot_module.BUTTON_SEARCH, LEADS_COMMAND_BUTTON],
        [bot_module.BUTTON_PIPELINE, bot_module.BUTTON_EXPORT],
        [bot_module.BUTTON_ANALYTICS],
        [ANALYZE_COMMAND_BUTTON, MESSAGE_COMMAND_BUTTON],
        [bot_module.BUTTON_RADARS, bot_module.BUTTON_LIMITS],
        [bot_module.BUTTON_PLANS],
        [bot_module.BUTTON_SUPPORT],
    ]


def install_hide_settings_button(bot_class: type[Any]) -> None:
    """Remove settings and expose reliable command-backed lead buttons."""
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

    bot_class._settings_button_hidden = True
