from __future__ import annotations

import re
from functools import wraps
from typing import Any

from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .bot import BUTTON_MESSAGE, MESSAGE_LEAD_ID, USER_INPUT_FILTER


def install_message_button_hotfix(bot_class: type[Any]) -> None:
    """Add a fallback conversation route only for the create-message button."""
    if getattr(bot_class, "_message_button_hotfix_installed", False):
        return

    original_build_application = bot_class.build_application

    @wraps(original_build_application)
    def build_application(self: Any):
        application = original_build_application(self)
        application.add_handler(
            ConversationHandler(
                entry_points=[
                    MessageHandler(
                        filters.Regex(rf"^{re.escape(BUTTON_MESSAGE)}$"),
                        self.message_start,
                    )
                ],
                states={
                    MESSAGE_LEAD_ID: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_lead_id)
                    ]
                },
                fallbacks=[CommandHandler("cancel", self.cancel)],
                allow_reentry=True,
                name="create_message_button_fallback",
            ),
            group=0,
        )
        return application

    bot_class.build_application = build_application
    bot_class._message_button_hotfix_installed = True
