from __future__ import annotations

import re
from functools import wraps
from typing import Any

from telegram import Update
from telegram.ext import (
    ApplicationHandlerStop,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from . import bot as bot_module


WAITING_FOR_LEAD_KEY = "_create_message_waiting_for_lead"


def install_message_button_hotfix(bot_class: type[Any]) -> None:
    """Route only the create-message button before competing conversations."""
    if getattr(bot_class, "_message_button_hotfix_v2_installed", False):
        return

    original_build_application = bot_class.build_application

    async def start_from_button(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        result = await self.message_start(update, context)
        if result == bot_module.MESSAGE_LEAD_ID:
            context.user_data[WAITING_FOR_LEAD_KEY] = True
        else:
            context.user_data.pop(WAITING_FOR_LEAD_KEY, None)
        raise ApplicationHandlerStop

    async def receive_lead_after_button(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not context.user_data.get(WAITING_FOR_LEAD_KEY):
            return

        message = update.effective_message
        text = (message.text or "").strip() if message else ""
        if text in bot_module.MENU_BUTTONS:
            context.user_data.pop(WAITING_FOR_LEAD_KEY, None)
            return

        result = await self.receive_lead_id(update, context)
        if result == ConversationHandler.END:
            context.user_data.pop(WAITING_FOR_LEAD_KEY, None)
        raise ApplicationHandlerStop

    @wraps(original_build_application)
    def build_application(self: Any):
        application = original_build_application(self)
        application.add_handler(
            MessageHandler(
                filters.Regex(
                    rf"^{re.escape(bot_module.BUTTON_MESSAGE)}$"
                ),
                self.start_message_from_button,
            ),
            group=-10,
        )
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.receive_message_lead_after_button,
            ),
            group=-10,
        )
        return application

    bot_class.start_message_from_button = start_from_button
    bot_class.receive_message_lead_after_button = receive_lead_after_button
    bot_class.build_application = build_application
    bot_class._message_button_hotfix_v2_installed = True
