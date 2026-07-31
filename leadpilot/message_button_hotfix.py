from __future__ import annotations

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


PENDING_ACTION_KEY = "_lead_action_pending"
ACTION_ANALYZE = "analyze"
ACTION_MESSAGE = "message"


def _button_key(value: object) -> str:
    """Normalize Telegram button text without changing visible labels."""
    text = " ".join(str(value or "").split()).strip()
    return text.replace("\ufe0f", "")


def install_message_button_hotfix(bot_class: type[Any]) -> None:
    """Route the three lead actions before competing conversation handlers."""
    if getattr(bot_class, "_lead_action_router_v3_installed", False):
        return

    original_build_application = bot_class.build_application

    leads_key = _button_key(bot_module.BUTTON_LEADS)
    analyze_key = _button_key(bot_module.BUTTON_ANALYZE)
    message_key = _button_key(bot_module.BUTTON_MESSAGE)
    menu_keys = {_button_key(value) for value in bot_module.MENU_BUTTONS}

    async def route_lead_actions(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        if message is None:
            return

        text = _button_key(message.text)
        pending = context.user_data.get(PENDING_ACTION_KEY)

        # A menu button always cancels the previous ID-waiting state first.
        if pending and text in menu_keys:
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

        if text == leads_key:
            context.user_data.pop(PENDING_ACTION_KEY, None)
            await self.list_leads(update, context)
            raise ApplicationHandlerStop()

        if text == analyze_key:
            result = await self.analyze_start(update, context)
            if result == bot_module.ANALYZE_LEAD_ID:
                context.user_data[PENDING_ACTION_KEY] = ACTION_ANALYZE
            else:
                context.user_data.pop(PENDING_ACTION_KEY, None)
            raise ApplicationHandlerStop()

        if text == message_key:
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
    bot_class._lead_action_router_v3_installed = True
