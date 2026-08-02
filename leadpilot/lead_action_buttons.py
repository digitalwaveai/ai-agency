from __future__ import annotations

import re
import unicodedata
from functools import wraps
from typing import Any

from telegram.ext import (
    ApplicationHandlerStop,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from . import bot as bot_module


ROUTING_GROUP = -10000
PENDING_KEY = "_lead_action_button_pending"
ACTION_ANALYZE = "analyze"
ACTION_MESSAGE = "message"


def _visible_words(value: object) -> str:
    """Return only visible lowercase words from a Telegram button label."""
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


def install_lead_action_buttons(bot_class: type[Any]) -> None:
    """Route three reply-keyboard actions through their existing working methods."""
    if getattr(bot_class, "_lead_action_buttons_installed", False):
        return

    original_build_application = bot_class.build_application

    target_actions = {
        _visible_words(bot_module.BUTTON_LEADS): "leads",
        _visible_words(bot_module.BUTTON_ANALYZE): ACTION_ANALYZE,
        _visible_words(bot_module.BUTTON_MESSAGE): ACTION_MESSAGE,
    }
    menu_keys = {
        _visible_words(button)
        for button in bot_module.MENU_BUTTONS
    }

    async def route_lead_action_buttons(
        self: Any,
        update: Any,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        if message is None or not message.text:
            return

        key = _visible_words(message.text)
        pending = context.user_data.get(PENDING_KEY)

        if pending and key in menu_keys:
            context.user_data.pop(PENDING_KEY, None)
            pending = None

        if pending == ACTION_ANALYZE:
            result = await self.receive_analyze_lead_id(update, context)
            if result != bot_module.ANALYZE_LEAD_ID:
                context.user_data.pop(PENDING_KEY, None)
            raise ApplicationHandlerStop()

        if pending == ACTION_MESSAGE:
            result = await self.receive_lead_id(update, context)
            if result != bot_module.MESSAGE_LEAD_ID:
                context.user_data.pop(PENDING_KEY, None)
            raise ApplicationHandlerStop()

        action = target_actions.get(key)
        if action is None:
            return

        if action == "leads":
            context.user_data.clear()
            await self.list_leads(update, context)
            raise ApplicationHandlerStop()

        if action == ACTION_ANALYZE:
            result = await self.analyze_start(update, context)
            if result == bot_module.ANALYZE_LEAD_ID:
                context.user_data[PENDING_KEY] = ACTION_ANALYZE
            else:
                context.user_data.pop(PENDING_KEY, None)
            raise ApplicationHandlerStop()

        result = await self.message_start(update, context)
        if result == bot_module.MESSAGE_LEAD_ID:
            context.user_data[PENDING_KEY] = ACTION_MESSAGE
        else:
            context.user_data.pop(PENDING_KEY, None)
        raise ApplicationHandlerStop()

    @wraps(original_build_application)
    def build_application(self: Any):
        application = original_build_application(self)
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.route_lead_action_buttons,
            ),
            group=ROUTING_GROUP,
        )
        return application

    bot_class.route_lead_action_buttons = route_lead_action_buttons
    bot_class.build_application = build_application
    bot_class._lead_action_buttons_installed = True
