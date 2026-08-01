from __future__ import annotations

import re
import sys
from typing import Any

from telegram import ReplyKeyboardMarkup
from telegram.ext import filters

from . import bot as bot_module


def install_hide_settings_button(bot_class: type[Any]) -> None:
    """Remove the technical settings button from every visible reply keyboard."""
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
        [
            [bot_module.BUTTON_NEW_PROJECT, bot_module.BUTTON_PROJECTS],
            [bot_module.BUTTON_SEARCH, bot_module.BUTTON_LEADS],
            [bot_module.BUTTON_PIPELINE, bot_module.BUTTON_EXPORT],
            [bot_module.BUTTON_ANALYTICS],
            [bot_module.BUTTON_ANALYZE, bot_module.BUTTON_MESSAGE],
            [bot_module.BUTTON_RADARS, bot_module.BUTTON_LIMITS],
            [bot_module.BUTTON_PLANS],
            [bot_module.BUTTON_SUPPORT],
        ],
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
