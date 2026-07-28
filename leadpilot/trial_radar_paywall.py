from __future__ import annotations

import asyncio
from functools import wraps
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler


TRIAL_RADAR_PLANS_CALLBACK = "trial_radar:plans"


def _plans_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⭐ Тарифы",
                    callback_data=TRIAL_RADAR_PLANS_CALLBACK,
                )
            ]
        ]
    )


def install_trial_radar_paywall(bot_class: type[Any]) -> None:
    """Show a clear tariff prompt when a trial user opens automatic radars."""
    if getattr(bot_class, "_trial_radar_paywall_installed", False):
        return

    old_radar_start = bot_class.radar_start
    old_build_application = bot_class.build_application

    @wraps(old_radar_start)
    async def radar_start(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        self.ensure_account(update)
        user = update.effective_user
        message = update.effective_message

        if user is not None and message is not None and not self.is_unlimited(update):
            access = await asyncio.to_thread(self.db.get_access_state, user.id)
            if access.get("active") and access.get("plan_code") == "trial":
                context.user_data.clear()
                await message.reply_text(
                    "📡 Радары недоступны в пробном тарифе.\n\n"
                    "Автоматический поиск клиентов доступен в тарифах Стандарт и Pro.\n"
                    "Откройте «⭐ Тарифы», чтобы подключить радар.",
                    reply_markup=_plans_keyboard(),
                )
                return ConversationHandler.END

        result = await old_radar_start(self, update, context)
        return result if isinstance(result, int) else ConversationHandler.END

    async def open_plans(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        await self.show_plans(update, context)

    @wraps(old_build_application)
    def build_application(self: Any):
        application = old_build_application(self)
        application.add_handler(
            CallbackQueryHandler(
                self.open_trial_radar_plans,
                pattern=rf"^{TRIAL_RADAR_PLANS_CALLBACK}$",
            ),
            group=-1,
        )
        return application

    bot_class.radar_start = radar_start
    bot_class.open_trial_radar_plans = open_plans
    bot_class.build_application = build_application
    bot_class._trial_radar_paywall_installed = True
