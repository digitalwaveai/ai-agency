from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .bot import MESSAGE_LEAD_ID, MENU, ROLE_LABELS


NICHE_CALLBACK = "niche_profile:add"
NICHE_PENDING_KEY = "_niche_profile_pending"
NICHE_MIN_LENGTH = 5
NICHE_MAX_LENGTH = 1000


def clean_niche(value: object) -> str:
    """Normalize a user's description without changing its meaning."""
    return " ".join(str(value or "").split()).strip()


def niche_keyboard(filled: bool) -> InlineKeyboardMarkup:
    label = "✏️ Изменить нишу" if filled else "➕ Добавить нишу"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=NICHE_CALLBACK)]]
    )


def install_niche_profile(database_class: type[Any], bot_class: type[Any]) -> None:
    """Add per-user niche storage and use it only in outreach generation."""
    if not getattr(database_class, "_niche_profile_storage_installed", False):
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
            with self._connect() as connection:  # noqa: SLF001
                connection.execute(statement)
                connection.commit()

        def get_user_niche(self: Any, user_id: int) -> str:
            statement = self._sql(
                "SELECT profile_text FROM user_profiles WHERE user_id = ?"
            )
            with self._connect() as connection:  # noqa: SLF001
                row = connection.execute(statement, (user_id,)).fetchone()
            return clean_niche(row["profile_text"] if row else "")

        def set_user_niche(self: Any, user_id: int, niche: str) -> str:
            cleaned = clean_niche(niche)
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
        database_class.get_user_niche = get_user_niche
        database_class.set_user_niche = set_user_niche
        database_class._niche_profile_storage_installed = True

    if getattr(bot_class, "_niche_profile_installed", False):
        return

    original_build_application = bot_class.build_application

    async def start(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        self.ensure_account(update)
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return

        context.user_data.pop(NICHE_PENDING_KEY, None)
        role = self.role(update)
        access = self.db.get_access_state(user.id)
        niche = await asyncio.to_thread(self.db.get_user_niche, user.id)

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

        await message.reply_text(
            "✨ LeadPilot AI\n\n"
            "AI-система поиска клиентов для специалистов и агентств.\n\n"
            f"{account_text}\n\n"
            "🧩 Ниша:\n"
            f"{niche or 'не заполнена'}",
            reply_markup=niche_keyboard(bool(niche)),
        )
        await message.reply_text("Выберите действие:", reply_markup=MENU)

    async def niche_start(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        query = update.callback_query
        user = update.effective_user
        if query is None or user is None:
            return

        await query.answer()
        self.ensure_account(update)
        context.user_data[NICHE_PENDING_KEY] = True
        current = await asyncio.to_thread(self.db.get_user_niche, user.id)
        current_text = f"\n\nСейчас указано:\n{current}" if current else ""

        if query.message is not None:
            await query.message.reply_text(
                "Напишите коротко о своём деле или нише: чем вы занимаетесь, "
                "что предлагаете клиентам и какую пользу им даёте.\n\n"
                "Это нужно, чтобы бот лучше создавал офферы по кнопке "
                "«✉️ Создать сообщение»."
                f"{current_text}\n\n"
                f"Объём: от {NICHE_MIN_LENGTH} до {NICHE_MAX_LENGTH} символов.",
                reply_markup=ReplyKeyboardRemove(),
            )
        raise ApplicationHandlerStop()

    async def receive_niche(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not context.user_data.get(NICHE_PENDING_KEY):
            return

        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            context.user_data.pop(NICHE_PENDING_KEY, None)
            raise ApplicationHandlerStop()

        niche = clean_niche(message.text)
        if len(niche) < NICHE_MIN_LENGTH:
            await message.reply_text(
                "Напишите немного подробнее — хотя бы несколько слов о своём деле."
            )
            raise ApplicationHandlerStop()
        if len(niche) > NICHE_MAX_LENGTH:
            await message.reply_text(
                f"Текст слишком длинный. Сократите его до {NICHE_MAX_LENGTH} символов."
            )
            raise ApplicationHandlerStop()

        saved = await asyncio.to_thread(self.db.set_user_niche, user.id, niche)
        context.user_data.pop(NICHE_PENDING_KEY, None)
        await message.reply_text(
            "✅ Ниша сохранена\n\n"
            f"{saved}\n\n"
            "Теперь бот будет учитывать её при создании офферов по кнопке "
            "«✉️ Создать сообщение».",
            reply_markup=MENU,
        )
        raise ApplicationHandlerStop()

    async def cancel_niche(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not context.user_data.get(NICHE_PENDING_KEY):
            return
        context.user_data.pop(NICHE_PENDING_KEY, None)
        if update.effective_message is not None:
            await update.effective_message.reply_text(
                "Добавление ниши отменено.", reply_markup=MENU
            )
        raise ApplicationHandlerStop()

    async def receive_lead_id(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        lead = await self._lead_from_message(update, MESSAGE_LEAD_ID)
        if lead is None:
            return MESSAGE_LEAD_ID

        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return ConversationHandler.END

        niche = await asyncio.to_thread(self.db.get_user_niche, user.id)
        await message.reply_text("Готовлю черновик обращения…")
        try:
            outreach = await asyncio.to_thread(
                self.outreach.generate,
                lead,
                niche,
            )
        except Exception:
            logging.exception("Outreach generation failed")
            outreach = self.outreach.fallback(lead, niche)
            outreach += (
                "\n\nOpenAI временно не ответил, поэтому показан базовый черновик."
            )
        await message.reply_text(outreach, reply_markup=MENU)
        return ConversationHandler.END

    @wraps(original_build_application)
    def build_application(self: Any):
        application = original_build_application(self)
        application.add_handler(
            CallbackQueryHandler(
                self.niche_profile_start,
                pattern=rf"^{NICHE_CALLBACK}$",
            ),
            group=-100,
        )
        application.add_handler(
            CommandHandler("cancel", self.cancel_niche_profile),
            group=-100,
        )
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.receive_niche_profile,
            ),
            group=-100,
        )
        return application

    bot_class.start = start
    bot_class.niche_profile_start = niche_start
    bot_class.receive_niche_profile = receive_niche
    bot_class.cancel_niche_profile = cancel_niche
    bot_class.receive_lead_id = receive_lead_id
    bot_class.build_application = build_application
    bot_class._niche_profile_installed = True
