from __future__ import annotations

import asyncio
from functools import wraps
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
)

from .bot import MENU, ROLE_LABELS, USER_INPUT_FILTER


PROFILE_TEXT = 9100
PROFILE_EDIT_CALLBACK = "personal_profile:edit"
PROFILE_MIN_LENGTH = 10
PROFILE_MAX_LENGTH = 1000


def _profile_keyboard(filled: bool) -> InlineKeyboardMarkup:
    label = "✏️ Изменить нишу" if filled else "✍️ Заполнить нишу"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=PROFILE_EDIT_CALLBACK)]]
    )


def _clean_profile(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def install_personal_profile(database_class: type[Any], bot_class: type[Any]) -> None:
    """Add a per-user profile used only for personalized outreach messages."""
    if getattr(database_class, "_personal_profile_installed", False):
        return

    original_init_schema = database_class.init_schema

    @wraps(original_init_schema)
    def init_schema(self: Any) -> None:
        original_init_schema(self)
        statement = """
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id BIGINT PRIMARY KEY,
                profile_text TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """
        with self._connect() as connection:  # noqa: SLF001 - project DB adapter
            connection.execute(statement)
            connection.commit()

    def get_user_profile(self: Any, user_id: int) -> str:
        statement = self._sql(
            "SELECT profile_text FROM user_profiles WHERE user_id = ?"
        )
        with self._connect() as connection:  # noqa: SLF001
            row = connection.execute(statement, (user_id,)).fetchone()
        return _clean_profile(row["profile_text"] if row else "")

    def set_user_profile(self: Any, user_id: int, profile_text: str) -> str:
        cleaned = _clean_profile(profile_text)
        statement = self._sql(
            """
            INSERT INTO user_profiles (user_id, profile_text)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                profile_text = excluded.profile_text,
                updated_at = CURRENT_TIMESTAMP
            """
        )
        with self._connect() as connection:  # noqa: SLF001
            connection.execute(statement, (user_id, cleaned))
            connection.commit()
        return cleaned

    database_class.init_schema = init_schema
    database_class.get_user_profile = get_user_profile
    database_class.set_user_profile = set_user_profile
    database_class._personal_profile_installed = True

    if getattr(bot_class, "_personal_profile_installed", False):
        return

    async def start(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        self.ensure_account(update)
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return

        role = self.role(update)
        access = await asyncio.to_thread(self.db.get_access_state, user.id)
        profile = await asyncio.to_thread(self.db.get_user_profile, user.id)

        if role in {"owner", "admin", "beta_tester"}:
            account_text = (
                "👤 Ваш аккаунт\n\n"
                f"Роль: {ROLE_LABELS[role]}\n"
                "Доступ: без лимитов тарифа\n"
                "Срок: бессрочно"
            )
        elif access["active"] and access["source"] == "stars":
            account_text = (
                "👤 Ваш аккаунт\n\n"
                "Роль: Пользователь\n"
                f"Тариф: {access['plan_name']}\n"
                f"Оплачен до: {access['ends_at'].strftime('%d.%m.%Y')}"
            )
        elif access["active"]:
            account_text = (
                "👤 Ваш аккаунт\n\n"
                "Роль: Пользователь\n"
                "Доступ: пробный тариф\n"
                f"Пробный период до: {access['ends_at'].strftime('%d.%m.%Y')}"
            )
        else:
            account_text = (
                "👤 Ваш аккаунт\n\n"
                "Роль: Пользователь\n"
                "Доступ: не активен\n"
                "Откройте «⭐ Тарифы» для выбора тарифа."
            )

        profile_text = profile or "не заполнено"
        await message.reply_text(
            "✨ LeadPilot AI\n\n"
            "AI-система поиска клиентов для специалистов и агентств.\n\n"
            f"{account_text}\n\n"
            "🧩 Чем я занимаюсь:\n"
            f"{profile_text}",
            reply_markup=_profile_keyboard(bool(profile)),
        )
        await message.reply_text("Выберите действие:", reply_markup=MENU)

    async def profile_start(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        query = update.callback_query
        user = update.effective_user
        if query is None or user is None:
            return ConversationHandler.END
        await query.answer()
        self.ensure_account(update)
        context.user_data.clear()
        current = await asyncio.to_thread(self.db.get_user_profile, user.id)
        current_text = (
            f"\n\nСейчас указано:\n{current}" if current else ""
        )
        if query.message is not None:
            await query.message.reply_text(
                "Кратко расскажите о себе: чем вы занимаетесь, что предлагаете "
                "клиентам и какую пользу им даёте.\n\n"
                "Это нужно, чтобы бот составлял персональные первые сообщения "
                "и правильно объяснял, кто вы и почему обращаетесь к клиенту."
                f"{current_text}\n\n"
                f"Объём: от {PROFILE_MIN_LENGTH} до {PROFILE_MAX_LENGTH} символов.",
                reply_markup=ReplyKeyboardRemove(),
            )
        return PROFILE_TEXT

    async def receive_profile(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return ConversationHandler.END
        profile = _clean_profile(message.text)
        if len(profile) < PROFILE_MIN_LENGTH:
            await message.reply_text(
                "Слишком коротко. Напишите хотя бы одним предложением, чем вы "
                "занимаетесь и чем полезны клиенту."
            )
            return PROFILE_TEXT
        if len(profile) > PROFILE_MAX_LENGTH:
            await message.reply_text(
                f"Текст слишком длинный. Сократите его до {PROFILE_MAX_LENGTH} символов."
            )
            return PROFILE_TEXT

        saved = await asyncio.to_thread(
            self.db.set_user_profile,
            user.id,
            profile,
        )
        context.user_data.clear()
        await message.reply_text(
            "✅ Ниша заполнена\n\n"
            f"Сохранено: {saved}\n\n"
            "Теперь бот будет учитывать этот текст при создании персональных "
            "сообщений клиентам.",
            reply_markup=MENU,
        )
        return ConversationHandler.END

    async def cancel_profile(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        context.user_data.clear()
        if update.effective_message is not None:
            await update.effective_message.reply_text(
                "Заполнение ниши отменено.",
                reply_markup=MENU,
            )
        return ConversationHandler.END

    async def receive_lead_id(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        lead = await self._lead_from_message(update, 4)
        if lead is None:
            return 4

        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return ConversationHandler.END
        profile = await asyncio.to_thread(self.db.get_user_profile, user.id)

        await message.reply_text("Готовлю персональное обращение…")
        try:
            outreach = await asyncio.to_thread(
                self.outreach.generate,
                lead,
                profile,
            )
        except Exception:
            import logging

            logging.exception("Outreach generation failed")
            outreach = self.outreach.fallback(lead, profile)
            outreach += (
                "\n\nOpenAI временно не ответил, поэтому показан базовый черновик."
            )
        await message.reply_text(outreach, reply_markup=MENU)
        return ConversationHandler.END

    original_build_application = bot_class.build_application

    @wraps(original_build_application)
    def build_application(self: Any):
        application = original_build_application(self)
        application.add_handler(
            ConversationHandler(
                entry_points=[
                    CallbackQueryHandler(
                        self.profile_start,
                        pattern=rf"^{PROFILE_EDIT_CALLBACK}$",
                    )
                ],
                states={
                    PROFILE_TEXT: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_personal_profile)
                    ]
                },
                fallbacks=[CommandHandler("cancel", self.cancel_personal_profile)],
                allow_reentry=True,
            ),
            group=-2,
        )
        return application

    bot_class.start = start
    bot_class.profile_start = profile_start
    bot_class.receive_personal_profile = receive_profile
    bot_class.cancel_personal_profile = cancel_profile
    bot_class.receive_lead_id = receive_lead_id
    bot_class.build_application = build_application
    bot_class._personal_profile_installed = True
