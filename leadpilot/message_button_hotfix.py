from __future__ import annotations

import re
import unicodedata
from functools import wraps
from typing import Any

from telegram.ext import ConversationHandler, filters

from . import bot as bot_module


ACTION_LEADS = "leads"
ACTION_ANALYZE = "analyze"
ACTION_MESSAGE = "message"


def _button_key(value: object) -> str:
    """Return normalized visible words from Telegram keyboard text."""
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


def _action_from_text(value: object) -> str | None:
    actions = {
        "мои лиды": ACTION_LEADS,
        "анализ клиента": ACTION_ANALYZE,
        "создать сообщение": ACTION_MESSAGE,
    }
    return actions.get(_button_key(value))


def _semantic_body(value: object) -> str | None:
    action = _action_from_text(value)
    labels = {
        ACTION_LEADS: "мои лиды",
        ACTION_ANALYZE: "анализ клиента",
        ACTION_MESSAGE: "создать сообщение",
    }
    label = labels.get(action)
    if label is None:
        return None

    # Accept any emoji, variation selector, invisible character or spacing
    # around and between the visible words of these three buttons.
    separator = r"[\W_\u200b-\u200f\u2060\ufeff]*"
    words = [re.escape(word) for word in label.split()]
    return separator + separator.join(words) + separator


def install_message_button_hotfix(bot_class: type[Any]) -> None:
    """Fix the core filters and menu dispatcher for the three lead buttons."""
    if getattr(bot_class, "_core_lead_button_routing_installed", False):
        return

    original_button_pattern = bot_module._button_pattern
    original_navigate_menu = bot_class.navigate_menu

    def button_pattern(text: str) -> str:
        semantic = _semantic_body(text)
        if semantic is not None:
            return rf"^(?iu:{semantic})$"
        return original_button_pattern(text)

    menu_bodies: list[str] = []
    for item in bot_module.MENU_BUTTONS:
        semantic = _semantic_body(item)
        menu_bodies.append(semantic if semantic is not None else re.escape(item))

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
        action = _action_from_text(message.text if message else "")
        if action is None:
            return await original_navigate_menu(self, update, context)

        context.user_data.clear()
        handlers = {
            ACTION_LEADS: self.list_leads,
            ACTION_ANALYZE: self.analyze_start,
            ACTION_MESSAGE: self.message_start,
        }
        result = await handlers[action](update, context)
        return result if isinstance(result, int) else ConversationHandler.END

    bot_class.navigate_menu = navigate_menu
    bot_class._core_lead_button_routing_installed = True
