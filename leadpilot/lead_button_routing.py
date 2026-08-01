from __future__ import annotations

import re
import unicodedata
from functools import wraps
from typing import Any

from telegram.ext import (
    ApplicationHandlerStop,
    ConversationHandler,
    MessageHandler,
    filters,
)

from . import bot as bot_module


TARGET_BUTTONS = {
    "мои лиды": bot_module.BUTTON_LEADS,
    "анализ клиента": bot_module.BUTTON_ANALYZE,
    "создать сообщение": bot_module.BUTTON_MESSAGE,
}
ROUTING_GROUP = -200


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


def semantic_pattern(value: object) -> str:
    body = semantic_body(value)
    if body is None:
        raise ValueError(f"Unsupported lead button: {value!r}")
    return rf"^(?iu:{body})$"


def _next_state(result: object) -> int:
    return result if isinstance(result, int) else ConversationHandler.END


def install_lead_button_routing(bot_class: type[Any]) -> None:
    """Give the three lead actions their own highest-priority conversation."""
    if getattr(bot_class, "_lead_button_routing_installed", False):
        return

    original_build_application = bot_class.build_application

    async def open_leads(self: Any, update: Any, context: Any) -> None:
        context.user_data.clear()
        await self.list_leads(update, context)
        raise ApplicationHandlerStop(ConversationHandler.END)

    async def open_analysis(self: Any, update: Any, context: Any) -> None:
        context.user_data.clear()
        result = await self.analyze_start(update, context)
        raise ApplicationHandlerStop(_next_state(result))

    async def open_message(self: Any, update: Any, context: Any) -> None:
        context.user_data.clear()
        result = await self.message_start(update, context)
        raise ApplicationHandlerStop(_next_state(result))

    async def receive_analysis_id(self: Any, update: Any, context: Any) -> None:
        result = await self.receive_analyze_lead_id(update, context)
        raise ApplicationHandlerStop(_next_state(result))

    async def receive_message_id(self: Any, update: Any, context: Any) -> None:
        result = await self.receive_lead_id(update, context)
        raise ApplicationHandlerStop(_next_state(result))

    @wraps(original_build_application)
    def build_application(self: Any):
        application = original_build_application(self)
        application.add_handler(
            ConversationHandler(
                entry_points=[
                    MessageHandler(
                        filters.Regex(semantic_pattern(bot_module.BUTTON_LEADS)),
                        self.open_leads_button,
                    ),
                    MessageHandler(
                        filters.Regex(semantic_pattern(bot_module.BUTTON_ANALYZE)),
                        self.open_analysis_button,
                    ),
                    MessageHandler(
                        filters.Regex(semantic_pattern(bot_module.BUTTON_MESSAGE)),
                        self.open_message_button,
                    ),
                ],
                states={
                    bot_module.ANALYZE_LEAD_ID: [
                        MessageHandler(
                            bot_module.USER_INPUT_FILTER,
                            self.receive_analysis_button_id,
                        )
                    ],
                    bot_module.MESSAGE_LEAD_ID: [
                        MessageHandler(
                            bot_module.USER_INPUT_FILTER,
                            self.receive_message_button_id,
                        )
                    ],
                },
                fallbacks=[],
                allow_reentry=True,
            ),
            group=ROUTING_GROUP,
        )
        return application

    bot_class.open_leads_button = open_leads
    bot_class.open_analysis_button = open_analysis
    bot_class.open_message_button = open_message
    bot_class.receive_analysis_button_id = receive_analysis_id
    bot_class.receive_message_button_id = receive_message_id
    bot_class.build_application = build_application
    bot_class._lead_button_routing_installed = True
