from __future__ import annotations

import re
import unicodedata
from functools import wraps
from typing import Any

from telegram.ext import ConversationHandler, filters

from . import bot as bot_module


TARGET_BUTTONS = {
    "мои лиды": bot_module.BUTTON_LEADS,
    "анализ клиента": bot_module.BUTTON_ANALYZE,
    "создать сообщение": bot_module.BUTTON_MESSAGE,
}


def button_key(value: object) -> str:
    """Normalize Telegram button text to its visible words."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = (
        text.replace("\ufe0f", "")
        .replace("\ufe0e", "")
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", " ")
        .replace("\u2060", "")
        .replace("\ufeff", "")
    )
    text = re.sub(r"[^0-9a-zа-яё]+", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def canonical_button(value: object) -> str | None:
    return TARGET_BUTTONS.get(button_key(value))


def semantic_body(value: object) -> str | None:
    """Build a regex body tolerant to emoji and invisible Unicode variants."""
    key = button_key(value)
    if key not in TARGET_BUTTONS:
        return None
    separator = r"[\W_\u200b-\u200f\u2060\ufeff]*"
    words = [re.escape(word) for word in key.split()]
    return separator + separator.join(words) + separator


def install_lead_button_routing(bot_class: type[Any]) -> None:
    """Make the three lead action buttons route by visible text, not bytes."""
    if getattr(bot_class, "_lead_button_routing_installed", False):
        return

    original_button_pattern = bot_module._button_pattern
    original_navigate_menu = bot_class.navigate_menu

    def button_pattern(text: str) -> str:
        body = semantic_body(text)
        if body is not None:
            return rf"^(?iu:{body})$"
        return original_button_pattern(text)

    menu_bodies: list[str] = []
    for item in bot_module.MENU_BUTTONS:
        body = semantic_body(item)
        menu_bodies.append(body if body is not None else re.escape(item))

    bot_module._button_pattern = button_pattern
    bot_module.MENU_BUTTON_PATTERN = rf"^(?iu:(?:{'|'.join(menu_bodies)}))$"
    bot_module.USER_INPUT_FILTER = (
        filters.TEXT
        & ~filters.COMMAND
        & ~filters.Regex(bot_module.MENU_BUTTON_PATTERN)
    )

    @wraps(original_navigate_menu)
    async def navigate_menu(self: Any, update: Any, context: Any) -> int:
        message = update.effective_message
        canonical = canonical_button(message.text if message else "")
        if canonical is None:
            return await original_navigate_menu(self, update, context)

        context.user_data.clear()
        handlers = {
            bot_module.BUTTON_LEADS: self.list_leads,
            bot_module.BUTTON_ANALYZE: self.analyze_start,
            bot_module.BUTTON_MESSAGE: self.message_start,
        }
        result = await handlers[canonical](update, context)
        return result if isinstance(result, int) else ConversationHandler.END

    bot_class.navigate_menu = navigate_menu
    bot_class._lead_button_routing_installed = True
