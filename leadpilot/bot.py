from __future__ import annotations

import asyncio
import logging

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .config import Settings
from .database import Database
from .models import Lead
from .outreach import OutreachGenerator
from .serpapi import SerpApiClient


NICHE, REGION, LIMIT, LEAD_ID = range(4)

MENU = ReplyKeyboardMarkup(
    [
        ["🔎 Найти лиды", "📋 Мои лиды"],
        ["✍️ Создать сообщение", "ℹ️ Помощь"],
    ],
    resize_keyboard=True,
)


class LeadPilotBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings.database_url)
        self.search_client = SerpApiClient(
            settings.serpapi_key, demo_mode=settings.demo_mode
        )
        self.outreach = OutreachGenerator(
            settings.openai_api_key, settings.openai_model
        )

    def authorized(self, update: Update) -> bool:
        owner_id = self.settings.owner_telegram_id
        user = update.effective_user
        return bool(user and (owner_id is None or user.id == owner_id))

    async def reject(self, update: Update) -> int:
        if update.effective_message:
            await update.effective_message.reply_text(
                f"Доступ ограничен. Поддержка: {self.settings.support_username}"
            )
        return ConversationHandler.END

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        await update.effective_message.reply_text(
            "LeadPilot в сети.\n\n"
            "Я нахожу компании через SerpAPI, сохраняю публичные контакты "
            "и помогаю подготовить первое обращение.",
            reply_markup=MENU,
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        mode = "демо" if self.settings.demo_mode else "реальный поиск"
        await update.effective_message.reply_text(
            f"✅ Бот работает. Режим: {mode}.", reply_markup=MENU
        )

    async def find_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        if not self.authorized(update):
            return await self.reject(update)
        await update.effective_message.reply_text(
            "Какую нишу или тип компаний ищем?\nНапример: стоматологии",
            reply_markup=ReplyKeyboardRemove(),
        )
        return NICHE

    async def receive_niche(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        context.user_data["niche"] = update.effective_message.text.strip()
        await update.effective_message.reply_text(
            "В каком городе или регионе искать?\nНапример: Казань"
        )
        return REGION

    async def receive_region(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        context.user_data["region"] = update.effective_message.text.strip()
        await update.effective_message.reply_text(
            "Сколько результатов показать? Введите число от 1 до 20."
        )
        return LIMIT

    async def receive_limit(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        try:
            limit = int(update.effective_message.text.strip())
            if not 1 <= limit <= 20:
                raise ValueError
        except ValueError:
            await update.effective_message.reply_text("Введите число от 1 до 20.")
            return LIMIT

        niche = str(context.user_data["niche"])
        region = str(context.user_data["region"])
        await update.effective_message.reply_text(
            f"Ищу: {niche}, {region}. Это может занять несколько секунд…"
        )
        try:
            leads = await asyncio.to_thread(
                self.search_client.search, niche, region, limit
            )
            if not leads:
                await update.effective_message.reply_text(
                    "По этому запросу результаты не найдены. "
                    "Попробуйте уточнить нишу или регион.",
                    reply_markup=MENU,
                )
                return ConversationHandler.END

            user_id = update.effective_user.id
            ids = await asyncio.to_thread(self.db.save_leads, user_id, leads)
            for lead, lead_id in zip(leads, ids, strict=True):
                lead.id = lead_id
            await update.effective_message.reply_text(
                self.format_leads(leads),
                reply_markup=MENU,
                disable_web_page_preview=True,
            )
        except Exception:
            logging.exception("Lead search failed")
            await update.effective_message.reply_text(
                "Не удалось выполнить поиск. Проверьте SERPAPI_KEY и логи Railway.",
                reply_markup=MENU,
            )
        return ConversationHandler.END

    async def list_leads(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        leads = await asyncio.to_thread(
            self.db.list_leads, update.effective_user.id, 10
        )
        if not leads:
            await update.effective_message.reply_text(
                "Сохранённых лидов пока нет. Сначала выполните поиск.",
                reply_markup=MENU,
            )
            return
        await update.effective_message.reply_text(
            self.format_leads(leads),
            reply_markup=MENU,
            disable_web_page_preview=True,
        )

    async def message_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        if not self.authorized(update):
            return await self.reject(update)
        leads = await asyncio.to_thread(
            self.db.list_leads, update.effective_user.id, 10
        )
        if not leads:
            await update.effective_message.reply_text(
                "Сначала найдите и сохраните хотя бы одного лида.",
                reply_markup=MENU,
            )
            return ConversationHandler.END
        await update.effective_message.reply_text(
            self.format_leads(leads)
            + "\n\nВведите ID лида, для которого подготовить сообщение.",
            reply_markup=ReplyKeyboardRemove(),
            disable_web_page_preview=True,
        )
        return LEAD_ID

    async def receive_lead_id(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        try:
            lead_id = int(update.effective_message.text.strip())
        except ValueError:
            await update.effective_message.reply_text("Введите числовой ID лида.")
            return LEAD_ID

        lead = await asyncio.to_thread(
            self.db.get_lead, update.effective_user.id, lead_id
        )
        if not lead:
            await update.effective_message.reply_text(
                "Лид с таким ID не найден. Попробуйте ещё раз."
            )
            return LEAD_ID

        await update.effective_message.reply_text("Готовлю черновик обращения…")
        try:
            message = await asyncio.to_thread(self.outreach.generate, lead)
        except Exception:
            logging.exception("Outreach generation failed")
            message = self.outreach.fallback(lead)
            message += (
                "\n\nOpenAI временно не ответил, поэтому показан базовый черновик."
            )
        await update.effective_message.reply_text(message, reply_markup=MENU)
        return ConversationHandler.END

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        await update.effective_message.reply_text(
            "Команды:\n"
            "/start — открыть меню\n"
            "/find — найти компании\n"
            "/leads — последние сохранённые лиды\n"
            "/message — подготовить обращение\n"
            "/status — проверить, что бот в сети\n"
            "/cancel — отменить текущий шаг\n\n"
            f"Поддержка: {self.settings.support_username}",
            reply_markup=MENU,
        )

    async def cancel(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        await update.effective_message.reply_text(
            "Действие отменено.", reply_markup=MENU
        )
        return ConversationHandler.END

    async def error(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        logging.exception("Telegram update failed", exc_info=context.error)

    @staticmethod
    def format_leads(leads: list[Lead]) -> str:
        blocks: list[str] = []
        for lead in leads:
            lines = [
                f"ID {lead.id} · {lead.name}",
                f"Релевантность: {lead.score}/100",
                f"Контакт: {lead.contact}",
            ]
            if lead.address:
                lines.append(f"Адрес: {lead.address}")
            if lead.source_url and not lead.source_url.startswith(("demo://", "serpapi://")):
                lines.append(f"Источник: {lead.source_url}")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def build_application(self) -> Application:
        application = Application.builder().token(
            self.settings.telegram_bot_token
        ).build()

        search_flow = ConversationHandler(
            entry_points=[
                CommandHandler("find", self.find_start),
                MessageHandler(filters.Regex(r"^🔎 Найти лиды$"), self.find_start),
            ],
            states={
                NICHE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_niche)
                ],
                REGION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_region)
                ],
                LIMIT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_limit)
                ],
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
        )
        message_flow = ConversationHandler(
            entry_points=[
                CommandHandler("message", self.message_start),
                MessageHandler(
                    filters.Regex(r"^✍️ Создать сообщение$"), self.message_start
                ),
            ],
            states={
                LEAD_ID: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_lead_id)
                ]
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
        )

        application.add_handler(search_flow)
        application.add_handler(message_flow)
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("status", self.status))
        application.add_handler(CommandHandler("leads", self.list_leads))
        application.add_handler(CommandHandler("help", self.help))
        application.add_handler(
            MessageHandler(filters.Regex(r"^📋 Мои лиды$"), self.list_leads)
        )
        application.add_handler(
            MessageHandler(filters.Regex(r"^ℹ️ Помощь$"), self.help)
        )
        application.add_error_handler(self.error)
        return application


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    bot = LeadPilotBot(settings)
    bot.db.init_schema()
    logging.info("LeadPilot starting in long-polling mode")
    bot.build_application().run_polling(drop_pending_updates=False)
