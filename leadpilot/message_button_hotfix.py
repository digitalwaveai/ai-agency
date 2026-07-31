from __future__ import annotations

import re
import unicodedata
from functools import wraps
from typing import Any

from telegram import Update
from telegram.ext import (
    ApplicationHandlerStop,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import bot as bot_module


PENDING_ACTION_KEY = "_lead_action_pending"
ACTION_LEADS = "leads"
ACTION_ANALYZE = "analyze"
ACTION_MESSAGE = "message"


def _button_key(value: object) -> str:
    """Return only normalized words from Telegram reply-keyboard text."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = (
        text.replace("\ufe0f", "")
        .replace("\ufe0e", "")
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", " ")
        .replace("\u2060", "")
    )
    text = re.sub(r"[^0-9a-zа-яё]+", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _action_from_text(value: object) -> str | None:
    """Recognize lead buttons by their visible words, not by emoji bytes."""
    key = _button_key(value)
    actions = {
        "мои лиды": ACTION_LEADS,
        "анализ клиента": ACTION_ANALYZE,
        "создать сообщение": ACTION_MESSAGE,
    }
    return actions.get(key)


def install_message_button_hotfix(bot_class: type[Any]) -> None:
    """Route the three lead actions before competing conversation handlers."""
    if getattr(bot_class, "_lead_action_router_v4_installed", False):
        return

    original_build_application = bot_class.build_application
    menu_keys = {_button_key(value) for value in bot_module.MENU_BUTTONS}

    async def route_lead_actions(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        if message is None:
            return

        text_key = _button_key(message.text)
        action = _action_from_text(message.text)
        pending = context.user_data.get(PENDING_ACTION_KEY)

        # Any menu button cancels the previous wait-for-ID state first.
        if pending and text_key in menu_keys:
            context.user_data.pop(PENDING_ACTION_KEY, None)
            pending = None

        if pending == ACTION_MESSAGE:
            result = await self.receive_lead_id(update, context)
            if result != bot_module.MESSAGE_LEAD_ID:
                context.user_data.pop(PENDING_ACTION_KEY, None)
            raise ApplicationHandlerStop()

        if pending == ACTION_ANALYZE:
            result = await self.receive_analyze_lead_id(update, context)
            if result != bot_module.ANALYZE_LEAD_ID:
                context.user_data.pop(PENDING_ACTION_KEY, None)
            raise ApplicationHandlerStop()

        if action == ACTION_LEADS:
            context.user_data.pop(PENDING_ACTION_KEY, None)
            await self.list_leads(update, context)
            raise ApplicationHandlerStop()

        if action == ACTION_ANALYZE:
            result = await self.analyze_start(update, context)
            if result == bot_module.ANALYZE_LEAD_ID:
                context.user_data[PENDING_ACTION_KEY] = ACTION_ANALYZE
            else:
                context.user_data.pop(PENDING_ACTION_KEY, None)
            raise ApplicationHandlerStop()

        if action == ACTION_MESSAGE:
            result = await self.message_start(update, context)
            if result == bot_module.MESSAGE_LEAD_ID:
                context.user_data[PENDING_ACTION_KEY] = ACTION_MESSAGE
            else:
                context.user_data.pop(PENDING_ACTION_KEY, None)
            raise ApplicationHandlerStop()

    @wraps(original_build_application)
    def build_application(self: Any):
        application = original_build_application(self)
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.route_lead_actions,
            ),
            group=-100,
        )
        return application

    bot_class.route_lead_actions = route_lead_actions
    bot_class.build_application = build_application
    bot_class._lead_action_router_v4_installed = True
