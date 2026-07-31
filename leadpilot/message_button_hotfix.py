from __future__ import annotations

import re
from functools import wraps
from typing import Any

from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from . import bot as bot_module


def install_message_button_hotfix(bot_class: type[Any]) -> None:
    """Restore the three lead-action buttons inside the main conversation."""
    if getattr(bot_class, "_lead_action_buttons_hotfix_installed", False):
        return

    original_build_application = bot_class.build_application

    async def route_lead_action_button(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        message = update.effective_message
        text = (message.text or "").strip() if message else ""

        if text == bot_module.BUTTON_LEADS:
            await self.list_leads(update, context)
            return ConversationHandler.END
        if text == bot_module.BUTTON_ANALYZE:
            return await self.analyze_start(update, context)
        if text == bot_module.BUTTON_MESSAGE:
            return await self.message_start(update, context)
        return ConversationHandler.END

    @wraps(original_build_application)
    def build_application(self: Any):
        application = original_build_application(self)

        main_conversation = None
        for handler in application.handlers.get(0, []):
            if not isinstance(handler, ConversationHandler):
                continue
            states = getattr(handler, "states", {})
            if (
                bot_module.MESSAGE_LEAD_ID in states
                and bot_module.ANALYZE_LEAD_ID in states
            ):
                main_conversation = handler
                break

        if main_conversation is not None:
            lead_buttons_pattern = rf"^(?:{'|'.join(re.escape(value) for value in (bot_module.BUTTON_LEADS, bot_module.BUTTON_ANALYZE, bot_module.BUTTON_MESSAGE))})$"
            main_conversation.entry_points.insert(
                0,
                MessageHandler(
                    filters.Regex(lead_buttons_pattern),
                    self.route_lead_action_button,
                ),
            )
            main_conversation.states[bot_module.MESSAGE_LEAD_ID] = [
                MessageHandler(
                    bot_module.USER_INPUT_FILTER,
                    self.receive_lead_id,
                )
            ]
            main_conversation.states[bot_module.ANALYZE_LEAD_ID] = [
                MessageHandler(
                    bot_module.USER_INPUT_FILTER,
                    self.receive_analyze_lead_id,
                )
            ]

        return application

    bot_class.route_lead_action_button = route_lead_action_button
    bot_class.build_application = build_application
    bot_class._lead_action_buttons_hotfix_installed = True
