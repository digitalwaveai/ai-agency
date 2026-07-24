from __future__ import annotations

import asyncio
import csv
import io
import logging
import re

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    LabeledPrice,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from .config import Settings
from .database import Database
from .models import Lead
from .outreach import OutreachGenerator
from .serpapi import SerpApiClient


(
    SEARCH_NICHE,
    SEARCH_REGION,
    SEARCH_LIMIT,
    MESSAGE_LEAD_ID,
    PROJECT_NAME,
    PROJECT_NICHE,
    PROJECT_REGION,
    ANALYZE_LEAD_ID,
    RADAR_NICHES,
    RADAR_REGIONS,
    RADAR_LIMIT,
) = range(11)

BUTTON_NEW_PROJECT = "➕ Новый проект"
BUTTON_PROJECTS = "📁 Мои проекты"
BUTTON_SEARCH = "🔎 Найти клиентов"
BUTTON_LEADS = "📋 Мои лиды"
BUTTON_PIPELINE = "📈 Воронка"
BUTTON_EXPORT = "📤 Экспорт лидов"
BUTTON_ANALYTICS = "📊 Аналитика лидов"
BUTTON_ANALYZE = "💎 Анализ клиента"
BUTTON_MESSAGE = "✉️ Создать сообщение"
BUTTON_RADARS = "📡 Радары"
BUTTON_LIMITS = "📊 Лимиты"
BUTTON_PLANS = "⭐ Тарифы"
BUTTON_SETTINGS = "⚙️ Настройки"
BUTTON_SUPPORT = "🛟 Поддержка"

MENU_BUTTONS = (
    BUTTON_NEW_PROJECT,
    BUTTON_PROJECTS,
    BUTTON_SEARCH,
    BUTTON_LEADS,
    BUTTON_PIPELINE,
    BUTTON_EXPORT,
    BUTTON_ANALYTICS,
    BUTTON_ANALYZE,
    BUTTON_MESSAGE,
    BUTTON_RADARS,
    BUTTON_LIMITS,
    BUTTON_PLANS,
    BUTTON_SETTINGS,
    BUTTON_SUPPORT,
)
MENU_BUTTON_PATTERN = rf"^(?:{'|'.join(re.escape(item) for item in MENU_BUTTONS)})$"
USER_INPUT_FILTER = (
    filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_BUTTON_PATTERN)
)

MENU = ReplyKeyboardMarkup(
    [
        [BUTTON_NEW_PROJECT, BUTTON_PROJECTS],
        [BUTTON_SEARCH, BUTTON_LEADS],
        [BUTTON_PIPELINE, BUTTON_EXPORT],
        [BUTTON_ANALYTICS],
        [BUTTON_ANALYZE, BUTTON_MESSAGE],
        [BUTTON_RADARS, BUTTON_LIMITS],
        [BUTTON_PLANS, BUTTON_SETTINGS],
        [BUTTON_SUPPORT],
    ],
    resize_keyboard=True,
    input_field_placeholder="Выберите действие",
)

STAR_TARIFFS = {
    ("standard", 1): ("Стандарт", 500),
    ("standard", 3): ("Стандарт", 1400),
    ("standard", 6): ("Стандарт", 2700),
    ("standard", 12): ("Стандарт", 5000),
    ("pro", 1): ("Pro", 1250),
    ("pro", 3): ("Pro", 3500),
    ("pro", 6): ("Pro", 6750),
    ("pro", 12): ("Pro", 12500),
}

PLAN_LIMITS = {
    "trial": (20, 20, 20, 20, 0),
    "standard": (100, 100, 100, 100, 3),
    "pro": (500, 500, 500, 500, 10),
}

TARIFFS_TEXT = (
    "⭐ Тарифы LeadPilot AI\n\n"
    "🎁 Пробный — 7 дней бесплатно\n"
    "20 поисков · 20 лидов · 20 анализов · 20 сообщений\n\n"
    "⭐ Стандарт\n"
    "100 поисков · 100 лидов · 100 анализов · 100 сообщений · 3 радара\n"
    "1 месяц — 990 ₽ или 500 ⭐\n"
    "3 месяца — 2 790 ₽ или 1 400 ⭐\n"
    "6 месяцев — 5 390 ₽ или 2 700 ⭐\n"
    "12 месяцев — 9 990 ₽ или 5 000 ⭐\n\n"
    "🚀 Pro\n"
    "500 поисков · 500 лидов · 500 анализов · 500 сообщений · 10 радаров\n"
    "1 месяц — 2 490 ₽ или 1 250 ⭐\n"
    "3 месяца — 6 990 ₽ или 3 500 ⭐\n"
    "6 месяцев — 13 490 ₽ или 6 750 ⭐\n"
    "12 месяцев — 24 990 ₽ или 12 500 ⭐\n\n"
    "Оплата разовая, без автопродления.\n"
    "Для оплаты звёздами выберите вариант ниже."
)

STAR_PAYMENT_KEYBOARD = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton(
                f"Стандарт {months} мес. · {stars:,} ⭐".replace(",", " "),
                callback_data=f"buy:standard:{months}",
            )
        ]
        for months, (_, stars) in (
            (months, STAR_TARIFFS[("standard", months)])
            for months in (1, 3, 6, 12)
        )
    ]
    + [
        [
            InlineKeyboardButton(
                f"Pro {months} мес. · {stars:,} ⭐".replace(",", " "),
                callback_data=f"buy:pro:{months}",
            )
        ]
        for months, (_, stars) in (
            (months, STAR_TARIFFS[("pro", months)])
            for months in (1, 3, 6, 12)
        )
    ]
)


def _button_pattern(text: str) -> str:
    return rf"^{re.escape(text)}$"


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

    def ensure_account(self, update: Update) -> None:
        user = update.effective_user
        if not user:
            return
        self.db.ensure_account(
            user.id,
            username=user.username or "",
            first_name=user.first_name or "",
        )

    def is_admin(self, update: Update) -> bool:
        owner_id = self.settings.owner_telegram_id
        user = update.effective_user
        return bool(user and owner_id is not None and user.id == owner_id)

    def authorized(self, update: Update) -> bool:
        user = update.effective_user
        if not user:
            return False
        self.ensure_account(update)
        if self.is_admin(update):
            return True
        return bool(self.db.get_access_state(user.id)["active"])

    async def reject(self, update: Update) -> int:
        if update.effective_message:
            await update.effective_message.reply_text(
                "Пробный или оплаченный доступ не активен.\n"
                "Откройте «⭐ Тарифы» для оплаты или «🛟 Поддержка», "
                "если доступ уже оплачен.",
                reply_markup=MENU,
            )
        return ConversationHandler.END

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self.ensure_account(update)
        user = update.effective_user
        is_admin = self.is_admin(update)
        access = self.db.get_access_state(user.id)
        if is_admin:
            account_text = (
                "👤 Ваш аккаунт\n\n"
                "Роль: Администратор\n"
                "Доступ: без лимитов тарифа\n"
                "Срок: бессрочно"
            )
        elif access["active"] and access["source"] == "stars":
            account_text = (
                "👤 Ваш аккаунт\n\n"
                "Роль: Клиент\n"
                f"Тариф: {access['plan_name']}\n"
                f"Оплачен до: {access['ends_at'].strftime('%d.%m.%Y')}"
            )
        elif access["active"]:
            account_text = (
                "👤 Ваш аккаунт\n\n"
                "Роль: Клиент\n"
                "Доступ: пробный тариф\n"
                f"Пробный период до: {access['ends_at'].strftime('%d.%m.%Y')}"
            )
        else:
            account_text = (
                "👤 Ваш аккаунт\n\n"
                "Роль: Клиент\n"
                "Доступ: не активен\n"
                "Откройте «⭐ Тарифы» для выбора тарифа."
            )
        await update.effective_message.reply_text(
            "✨ LeadPilot AI\n\n"
            "AI-система поиска клиентов для специалистов и агентств.\n\n"
            f"{account_text}\n\n"
            "Выберите действие:",
            reply_markup=MENU,
        )

    async def menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        await update.effective_message.reply_text(
            "Главное меню:", reply_markup=MENU
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        mode = "демо" if self.settings.demo_mode else "реальный поиск"
        await update.effective_message.reply_text(
            f"✅ Бот работает 24/7. Режим: {mode}.", reply_markup=MENU
        )

    async def find_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        if not self.authorized(update):
            return await self.reject(update)
        context.user_data.clear()
        await update.effective_message.reply_text(
            "🔎 Найти клиентов\n\n"
            "Какую нишу или тип компаний ищем?\n"
            "Например: стоматологии",
            reply_markup=ReplyKeyboardRemove(),
        )
        return SEARCH_NICHE

    async def receive_niche(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        niche = (update.effective_message.text or "").strip()
        if len(niche) < 2:
            await update.effective_message.reply_text(
                "Укажите нишу минимум двумя символами."
            )
            return SEARCH_NICHE
        context.user_data["niche"] = niche
        await update.effective_message.reply_text(
            "В каком городе или регионе искать?\nНапример: Казань"
        )
        return SEARCH_REGION

    async def receive_region(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        region = (update.effective_message.text or "").strip()
        if len(region) < 2:
            await update.effective_message.reply_text(
                "Укажите город или регион минимум двумя символами."
            )
            return SEARCH_REGION
        context.user_data["region"] = region
        await update.effective_message.reply_text(
            "Сколько результатов показать? Введите число от 1 до 20."
        )
        return SEARCH_LIMIT

    async def receive_limit(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        try:
            limit = int((update.effective_message.text or "").strip())
            if not 1 <= limit <= 20:
                raise ValueError
        except ValueError:
            await update.effective_message.reply_text("Введите число от 1 до 20.")
            return SEARCH_LIMIT

        niche = str(context.user_data["niche"])
        region = str(context.user_data["region"])
        await update.effective_message.reply_text(
            f"Ищу: {niche}, {region}. Это может занять несколько секунд…"
        )
        await self._search_and_reply(update, niche, region, limit)
        return ConversationHandler.END

    async def _search_and_reply(
        self, update: Update, niche: str, region: str, limit: int
    ) -> list[Lead]:
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
                return []

            user_id = update.effective_user.id
            ids = await asyncio.to_thread(self.db.save_leads, user_id, leads)
            for lead, lead_id in zip(leads, ids, strict=True):
                lead.id = lead_id
            await update.effective_message.reply_text(
                self.format_leads(leads),
                reply_markup=MENU,
                disable_web_page_preview=True,
            )
            return leads
        except Exception:
            logging.exception("Lead search failed")
            await update.effective_message.reply_text(
                "Не удалось выполнить поиск. Проверьте SerpAPI и логи Railway.",
                reply_markup=MENU,
            )
            return []

    async def list_leads(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        leads = await asyncio.to_thread(
            self.db.list_leads, update.effective_user.id, 15
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

    async def new_project_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        if not self.authorized(update):
            return await self.reject(update)
        context.user_data.clear()
        await update.effective_message.reply_text(
            "➕ Новый проект\n\n"
            "Как назвать проект?\n"
            "Например: Клиники Москвы",
            reply_markup=ReplyKeyboardRemove(),
        )
        return PROJECT_NAME

    async def receive_project_name(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        name = (update.effective_message.text or "").strip()
        if not 3 <= len(name) <= 120:
            await update.effective_message.reply_text(
                "Название должно содержать от 3 до 120 символов."
            )
            return PROJECT_NAME
        context.user_data["project_name"] = name
        await update.effective_message.reply_text(
            "Какая ниша у проекта?\nНапример: стоматологии"
        )
        return PROJECT_NICHE

    async def receive_project_niche(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        niche = (update.effective_message.text or "").strip()
        if len(niche) < 2:
            await update.effective_message.reply_text("Укажите нишу точнее.")
            return PROJECT_NICHE
        context.user_data["project_niche"] = niche
        await update.effective_message.reply_text(
            "Основной город или регион проекта?\nНапример: Москва"
        )
        return PROJECT_REGION

    async def receive_project_region(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        region = (update.effective_message.text or "").strip()
        if len(region) < 2:
            await update.effective_message.reply_text("Укажите регион точнее.")
            return PROJECT_REGION
        project_id = await asyncio.to_thread(
            self.db.create_project,
            update.effective_user.id,
            str(context.user_data["project_name"]),
            str(context.user_data["project_niche"]),
            region,
        )
        await update.effective_message.reply_text(
            "✅ Проект создан\n\n"
            f"ID {project_id} · {context.user_data['project_name']}\n"
            f"Ниша: {context.user_data['project_niche']}\n"
            f"Регион: {region}",
            reply_markup=MENU,
        )
        return ConversationHandler.END

    async def list_projects(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        projects = await asyncio.to_thread(
            self.db.list_projects, update.effective_user.id, 20
        )
        if not projects:
            await update.effective_message.reply_text(
                "Проектов пока нет. Нажмите «➕ Новый проект».",
                reply_markup=MENU,
            )
            return
        text = ["📁 Мои проекты", ""]
        for project in projects:
            text.append(
                f"ID {project['id']} · {project['name']}\n"
                f"Ниша: {project['niche']} · Регион: {project['region']}"
            )
        await update.effective_message.reply_text(
            "\n\n".join(text), reply_markup=MENU
        )

    async def message_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        if not self.authorized(update):
            return await self.reject(update)
        context.user_data.clear()
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
        return MESSAGE_LEAD_ID

    async def receive_lead_id(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        lead = await self._lead_from_message(update, MESSAGE_LEAD_ID)
        if lead is None:
            return MESSAGE_LEAD_ID

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

    async def analyze_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        if not self.authorized(update):
            return await self.reject(update)
        context.user_data.clear()
        leads = await asyncio.to_thread(
            self.db.list_leads, update.effective_user.id, 10
        )
        if not leads:
            await update.effective_message.reply_text(
                "Сначала найдите хотя бы одного клиента.", reply_markup=MENU
            )
            return ConversationHandler.END
        await update.effective_message.reply_text(
            self.format_leads(leads)
            + "\n\nВведите ID клиента для подробного анализа.",
            reply_markup=ReplyKeyboardRemove(),
            disable_web_page_preview=True,
        )
        return ANALYZE_LEAD_ID

    async def receive_analyze_lead_id(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        lead = await self._lead_from_message(update, ANALYZE_LEAD_ID)
        if lead is None:
            return ANALYZE_LEAD_ID
        strengths: list[str] = []
        gaps: list[str] = []
        if lead.website:
            strengths.append("есть сайт")
        else:
            gaps.append("сайт не найден")
        if lead.phone:
            strengths.append("есть публичный телефон")
        else:
            gaps.append("телефон не найден")
        if lead.address:
            strengths.append("есть локальная привязка")
        if lead.score >= 80:
            strengths.append("высокая релевантность")
        elif lead.score < 50:
            gaps.append("низкая релевантность запросу")
        text = (
            f"💎 Анализ клиента · ID {lead.id}\n\n"
            f"Компания: {lead.name}\n"
            f"Рейтинг: {lead.score}/100\n"
            f"Контакт: {lead.contact}\n"
            f"Адрес: {lead.address or 'не найден'}\n"
            f"Описание: {lead.snippet or 'нет данных'}\n\n"
            f"Сильные сигналы: {', '.join(strengths) or 'не обнаружены'}\n"
            f"Что проверить: {', '.join(gaps) or 'критичных пробелов нет'}\n\n"
            "Следующий шаг: проверьте источник и подготовьте персональное "
            "сообщение без массовой рассылки."
        )
        if lead.source_url and not lead.source_url.startswith(
            ("demo://", "serpapi://")
        ):
            text += f"\nИсточник: {lead.source_url}"
        await update.effective_message.reply_text(
            text, reply_markup=MENU, disable_web_page_preview=True
        )
        return ConversationHandler.END

    async def _lead_from_message(
        self, update: Update, state: int
    ) -> Lead | None:
        try:
            lead_id = int((update.effective_message.text or "").strip())
        except ValueError:
            await update.effective_message.reply_text("Введите числовой ID.")
            return None
        lead = await asyncio.to_thread(
            self.db.get_lead, update.effective_user.id, lead_id
        )
        if not lead:
            await update.effective_message.reply_text(
                "Лид с таким ID не найден. Попробуйте ещё раз."
            )
            return None
        return lead

    async def radar_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        if not self.authorized(update):
            return await self.reject(update)
        context.user_data.clear()
        radars = await asyncio.to_thread(
            self.db.list_radars, update.effective_user.id, 10
        )
        existing = ""
        if radars:
            rows = []
            for radar in radars:
                niches = ", ".join(str(radar["niches"]).splitlines())
                regions = ", ".join(str(radar["regions"]).splitlines())
                rows.append(f"ID {radar['id']} · {niches} · {regions}")
            existing = (
                "Сохранённые радары:\n"
                + "\n".join(rows)
                + "\nЗапуск сохранённого: /radar_run ID\n\n"
            )
        await update.effective_message.reply_text(
            "📡 Много-радар\n\n"
            f"{existing}"
            "Введите одну или несколько ниш через запятую.\n"
            "Пример: стоматологии, косметологии",
            reply_markup=ReplyKeyboardRemove(),
        )
        return RADAR_NICHES

    async def receive_radar_niches(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        niches = self._split_values(update.effective_message.text)
        if not niches:
            await update.effective_message.reply_text(
                "Введите хотя бы одну нишу."
            )
            return RADAR_NICHES
        context.user_data["radar_niches"] = niches[:3]
        await update.effective_message.reply_text(
            "Введите города или регионы через запятую.\n"
            "Пример: Москва, Казань"
        )
        return RADAR_REGIONS

    async def receive_radar_regions(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        regions = self._split_values(update.effective_message.text)
        if not regions:
            await update.effective_message.reply_text(
                "Введите хотя бы один регион."
            )
            return RADAR_REGIONS
        context.user_data["radar_regions"] = regions[:3]
        await update.effective_message.reply_text(
            "Сколько лидов брать по каждой комбинации? Введите число от 1 до 5."
        )
        return RADAR_LIMIT

    async def receive_radar_limit(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        try:
            result_limit = int((update.effective_message.text or "").strip())
            if not 1 <= result_limit <= 5:
                raise ValueError
        except ValueError:
            await update.effective_message.reply_text("Введите число от 1 до 5.")
            return RADAR_LIMIT

        niches = list(context.user_data["radar_niches"])
        regions = list(context.user_data["radar_regions"])
        radar_id = await asyncio.to_thread(
            self.db.create_radar,
            update.effective_user.id,
            niches,
            regions,
            result_limit,
        )
        return await self._run_radar(
            update,
            radar_id=radar_id,
            niches=niches,
            regions=regions,
            result_limit=result_limit,
            saved_now=True,
        )

    async def radar_run(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        if not self.authorized(update):
            return await self.reject(update)
        if len(context.args) != 1:
            await update.effective_message.reply_text(
                "Формат: /radar_run ID", reply_markup=MENU
            )
            return ConversationHandler.END
        try:
            radar_id = int(context.args[0])
        except ValueError:
            await update.effective_message.reply_text(
                "ID радара должен быть числом.", reply_markup=MENU
            )
            return ConversationHandler.END
        radar = await asyncio.to_thread(
            self.db.get_radar, update.effective_user.id, radar_id
        )
        if not radar:
            await update.effective_message.reply_text(
                "Радар с таким ID не найден.", reply_markup=MENU
            )
            return ConversationHandler.END
        return await self._run_radar(
            update,
            radar_id=radar_id,
            niches=str(radar["niches"]).splitlines(),
            regions=str(radar["regions"]).splitlines(),
            result_limit=int(radar["result_limit"]),
            saved_now=False,
        )

    async def _run_radar(
        self,
        update: Update,
        *,
        radar_id: int,
        niches: list[str],
        regions: list[str],
        result_limit: int,
        saved_now: bool,
    ) -> int:
        combinations = [(niche, region) for niche in niches for region in regions][
            :6
        ]
        prefix = "сохранён" if saved_now else "запущен"
        await update.effective_message.reply_text(
            f"📡 Радар ID {radar_id} {prefix}. "
            f"Запускаю {len(combinations)} поисковых направлений…"
        )
        collected: list[Lead] = []
        try:
            for niche, region in combinations:
                leads = await asyncio.to_thread(
                    self.search_client.search, niche, region, result_limit
                )
                if not leads:
                    continue
                ids = await asyncio.to_thread(
                    self.db.save_leads, update.effective_user.id, leads
                )
                for lead, lead_id in zip(leads, ids, strict=True):
                    lead.id = lead_id
                collected.extend(leads)
        except Exception:
            logging.exception("Multi-radar search failed")
            await update.effective_message.reply_text(
                "Один из поисков радара не завершился. "
                "Проверьте SerpAPI и повторите позже.",
                reply_markup=MENU,
            )
            return ConversationHandler.END

        unique: dict[int, Lead] = {
            int(lead.id): lead for lead in collected if lead.id is not None
        }
        if not unique:
            await update.effective_message.reply_text(
                "Радар отработал, но новых результатов не найдено.",
                reply_markup=MENU,
            )
            return ConversationHandler.END
        visible = list(unique.values())[:10]
        await update.effective_message.reply_text(
            f"✅ Радар завершён. Найдено и сохранено: {len(unique)}\n\n"
            + self.format_leads(visible),
            reply_markup=MENU,
            disable_web_page_preview=True,
        )
        return ConversationHandler.END

    async def show_pipeline(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        stats = await asyncio.to_thread(
            self.db.lead_statistics, update.effective_user.id
        )
        await update.effective_message.reply_text(
            "📈 Воронка\n\n"
            f"Новые: {stats['new_count']}\n"
            f"Связались: {stats['contacted_count']}\n"
            f"Ответили: {stats['replied_count']}\n\n"
            "Статус можно изменить командой:\n"
            "/lead_status ID new|contacted|replied",
            reply_markup=MENU,
        )

    async def lead_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        if len(context.args) != 2:
            await update.effective_message.reply_text(
                "Формат: /lead_status ID new|contacted|replied",
                reply_markup=MENU,
            )
            return
        try:
            lead_id = int(context.args[0])
        except ValueError:
            await update.effective_message.reply_text("ID должен быть числом.")
            return
        status = context.args[1].lower()
        allowed = {"new", "contacted", "replied"}
        if status not in allowed:
            await update.effective_message.reply_text(
                "Статус: new, contacted или replied."
            )
            return
        changed = await asyncio.to_thread(
            self.db.update_lead_status,
            update.effective_user.id,
            lead_id,
            status,
        )
        await update.effective_message.reply_text(
            (
                f"✅ Статус лида ID {lead_id}: {status}"
                if changed
                else "Лид с таким ID не найден."
            ),
            reply_markup=MENU,
        )

    async def show_analytics(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        stats = await asyncio.to_thread(
            self.db.lead_statistics, update.effective_user.id
        )
        await update.effective_message.reply_text(
            "📊 Аналитика лидов\n\n"
            f"Всего сохранено: {stats['total']}\n"
            f"Средняя релевантность: {stats['average_score']}/100\n"
            f"С рейтингом 80+: {stats['high_score']}\n"
            f"С публичным контактом: {stats['with_contacts']}",
            reply_markup=MENU,
        )

    async def export_leads(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        leads = await asyncio.to_thread(
            self.db.list_leads, update.effective_user.id, 500
        )
        if not leads:
            await update.effective_message.reply_text(
                "Нет лидов для экспорта.", reply_markup=MENU
            )
            return
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(
            [
                "id",
                "name",
                "score",
                "status",
                "phone",
                "website",
                "address",
                "source_url",
                "query",
            ]
        )
        for lead in leads:
            writer.writerow(
                [
                    lead.id,
                    lead.name,
                    lead.score,
                    lead.status,
                    lead.phone,
                    lead.website,
                    lead.address,
                    lead.source_url,
                    lead.query,
                ]
            )
        document = InputFile(
            stream.getvalue().encode("utf-8-sig"),
            filename="leadpilot_leads.csv",
        )
        await update.effective_message.reply_document(
            document=document,
            caption=f"📤 Экспортировано лидов: {len(leads)}",
            reply_markup=MENU,
        )

    async def show_limits(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        self.ensure_account(update)
        if self.is_admin(update):
            await update.effective_message.reply_text(
                "📊 Ваши лимиты\n\n"
                "Роль: Администратор\n"
                "Лимиты тарифа: не применяются\n"
                "Срок доступа: бессрочно",
                reply_markup=MENU,
            )
            return

        access = self.db.get_access_state(update.effective_user.id)
        if not access["active"]:
            await update.effective_message.reply_text(
                "📊 Ваши лимиты\n\n"
                "Активного тарифа нет.\n"
                "Откройте «⭐ Тарифы», чтобы продолжить работу.",
                reply_markup=MENU,
            )
            return

        searches, leads, analyses, messages, radars = PLAN_LIMITS[
            access["plan_code"]
        ]
        await update.effective_message.reply_text(
            "📊 Ваши лимиты\n\n"
            f"Тариф: {access['plan_name']}\n"
            f"Действует до: {access['ends_at'].strftime('%d.%m.%Y')}\n\n"
            f"Поиски: {searches}\n"
            f"Лиды: {leads}\n"
            f"Анализы: {analyses}\n"
            f"Сообщения: {messages}\n"
            f"Радары: {radars}",
            reply_markup=MENU,
        )

    async def show_plans(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        self.ensure_account(update)
        await update.effective_message.reply_text(TARIFFS_TEXT, reply_markup=MENU)
        await update.effective_message.reply_text(
            "Выберите тариф и срок для оплаты Telegram Stars:",
            reply_markup=STAR_PAYMENT_KEYBOARD,
        )

    async def select_star_payment(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if not query or not query.from_user:
            return
        await query.answer()
        self.db.ensure_account(
            query.from_user.id,
            username=query.from_user.username or "",
            first_name=query.from_user.first_name or "",
        )
        try:
            _, plan_code, months_raw = (query.data or "").split(":")
            months = int(months_raw)
            plan_name, stars = STAR_TARIFFS[(plan_code, months)]
        except (ValueError, KeyError):
            await query.message.reply_text(
                "Не удалось определить тариф. Откройте «⭐ Тарифы» ещё раз.",
                reply_markup=MENU,
            )
            return

        payload = f"leadpilot|{plan_code}|{months}|{query.from_user.id}"
        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title=f"LeadPilot {plan_name} — {months} мес.",
            description=(
                f"Доступ к тарифу {plan_name} на {months} мес. "
                "Разовая оплата без автопродления."
            ),
            payload=payload,
            currency="XTR",
            prices=[LabeledPrice(f"{plan_name}, {months} мес.", stars)],
        )

    async def precheckout(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.pre_checkout_query
        if not query:
            return
        parsed = self._parse_payment_payload(query.invoice_payload)
        if not parsed:
            await query.answer(
                ok=False,
                error_message="Счёт не относится к LeadPilot. Создайте новый.",
            )
            return
        plan_code, months, user_id = parsed
        expected = STAR_TARIFFS.get((plan_code, months))
        valid = bool(
            expected
            and query.from_user.id == user_id
            and query.currency == "XTR"
            and query.total_amount == expected[1]
        )
        if not valid:
            await query.answer(
                ok=False,
                error_message="Параметры счёта изменились. Создайте новый счёт.",
            )
            return
        await query.answer(ok=True)

    async def successful_payment(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.effective_message
        payment = message.successful_payment if message else None
        user = update.effective_user
        if not payment or not user:
            return
        parsed = self._parse_payment_payload(payment.invoice_payload)
        if not parsed:
            logging.error("Unknown successful payment payload")
            await message.reply_text(
                "Платёж получен, но тариф не распознан. "
                f"Напишите в поддержку: {self.settings.support_username}",
                reply_markup=MENU,
            )
            return
        plan_code, months, payload_user_id = parsed
        expected = STAR_TARIFFS.get((plan_code, months))
        if (
            not expected
            or payload_user_id != user.id
            or payment.currency != "XTR"
            or payment.total_amount != expected[1]
        ):
            logging.error("Successful payment validation failed")
            await message.reply_text(
                "Платёж получен, но его параметры не совпали со счётом. "
                f"Напишите в поддержку: {self.settings.support_username}",
                reply_markup=MENU,
            )
            return

        plan_name, stars = expected
        ends_at = await asyncio.to_thread(
            self.db.record_star_payment,
            user.id,
            plan_code,
            months,
            stars,
            payment.telegram_payment_charge_id,
            payment.provider_payment_charge_id,
        )
        await message.reply_text(
            "✅ Оплата подтверждена\n\n"
            f"Тариф: {plan_name}\n"
            f"Срок: {months} мес.\n"
            f"Оплачено: {stars:,} ⭐\n".replace(",", " ")
            + f"Доступ действует до: {ends_at.strftime('%d.%m.%Y')}",
            reply_markup=MENU,
        )

    async def show_settings(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        mode = "демо" if self.settings.demo_mode else "реальный поиск"
        await update.effective_message.reply_text(
            "⚙️ Настройки\n\n"
            f"Поиск: SerpAPI\n"
            f"Режим: {mode}\n"
            f"Модель сообщений: {self.settings.openai_model}\n"
            "Повторный запуск сохранённого радара: /radar_run ID\n\n"
            "Секретные ключи хранятся в Railway и здесь не показываются.",
            reply_markup=MENU,
        )

    async def show_support(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        self.ensure_account(update)
        username = self.settings.support_username.lstrip("@")
        support_keyboard = (
            InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Написать в Telegram",
                            url=f"https://t.me/{username}",
                        )
                    ]
                ]
            )
            if username
            else None
        )
        await update.effective_message.reply_text(
            "🛟 Поддержка\n\n"
            f"Telegram: {self.settings.support_username}\n"
            f"Email: {self.settings.support_email}\n\n"
            "Не отправляйте токены, пароли и данные банковских карт.",
            reply_markup=support_keyboard or MENU,
        )

    async def show_help(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        await update.effective_message.reply_text(
            "Команды:\n"
            "/start — приветствие и меню\n"
            "/menu — главное меню\n"
            "/find — найти клиентов\n"
            "/projects — мои проекты\n"
            "/leads — последние лиды\n"
            "/message — создать обращение\n"
            "/radars — создать много-радар\n"
            "/radar_run ID — запустить сохранённый радар\n"
            "/export — выгрузить CSV\n"
            "/analytics — аналитика\n"
            "/status — проверить работу бота\n"
            "/cancel — отменить текущий шаг\n\n"
            f"Поддержка: {self.settings.support_username}",
            reply_markup=MENU,
        )

    async def navigate_menu(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        context.user_data.clear()
        handlers = {
            BUTTON_NEW_PROJECT: self.new_project_start,
            BUTTON_PROJECTS: self.list_projects,
            BUTTON_SEARCH: self.find_start,
            BUTTON_LEADS: self.list_leads,
            BUTTON_PIPELINE: self.show_pipeline,
            BUTTON_EXPORT: self.export_leads,
            BUTTON_ANALYTICS: self.show_analytics,
            BUTTON_ANALYZE: self.analyze_start,
            BUTTON_MESSAGE: self.message_start,
            BUTTON_RADARS: self.radar_start,
            BUTTON_LIMITS: self.show_limits,
            BUTTON_PLANS: self.show_plans,
            BUTTON_SETTINGS: self.show_settings,
            BUTTON_SUPPORT: self.show_support,
        }
        handler = handlers.get(update.effective_message.text or "")
        if not handler:
            return ConversationHandler.END
        result = await handler(update, context)
        return result if isinstance(result, int) else ConversationHandler.END

    async def navigate_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        context.user_data.clear()
        raw_command = (update.effective_message.text or "").split(maxsplit=1)[0]
        command = raw_command.lstrip("/").split("@", maxsplit=1)[0].lower()
        handlers = {
            "start": self.start,
            "menu": self.menu,
            "status": self.status,
            "new_project": self.new_project_start,
            "projects": self.list_projects,
            "find": self.find_start,
            "leads": self.list_leads,
            "message": self.message_start,
            "analyze": self.analyze_start,
            "radars": self.radar_start,
            "radar_run": self.radar_run,
            "export": self.export_leads,
            "analytics": self.show_analytics,
            "plans": self.show_plans,
            "limits": self.show_limits,
            "support": self.show_support,
            "help": self.show_help,
            "lead_status": self.lead_status,
        }
        handler = handlers.get(command)
        if not handler:
            return ConversationHandler.END
        result = await handler(update, context)
        return result if isinstance(result, int) else ConversationHandler.END

    async def cancel(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        context.user_data.clear()
        await update.effective_message.reply_text(
            "Действие отменено.", reply_markup=MENU
        )
        return ConversationHandler.END

    async def error(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        logging.exception("Telegram update failed", exc_info=context.error)

    @staticmethod
    def _split_values(value: str | None) -> list[str]:
        values = [
            item.strip()
            for item in (value or "").replace(";", ",").split(",")
            if item.strip()
        ]
        return list(dict.fromkeys(values))

    @staticmethod
    def _parse_payment_payload(payload: str) -> tuple[str, int, int] | None:
        try:
            prefix, plan_code, months_raw, user_id_raw = payload.split("|")
            months = int(months_raw)
            user_id = int(user_id_raw)
        except (ValueError, AttributeError):
            return None
        if prefix != "leadpilot" or (plan_code, months) not in STAR_TARIFFS:
            return None
        return plan_code, months, user_id

    @staticmethod
    def format_leads(leads: list[Lead]) -> str:
        blocks: list[str] = []
        for lead in leads:
            lines = [
                f"ID {lead.id} · {lead.name}",
                f"Релевантность: {lead.score}/100",
                f"Статус: {lead.status}",
                f"Контакт: {lead.contact}",
            ]
            if lead.address:
                lines.append(f"Адрес: {lead.address}")
            if lead.source_url and not lead.source_url.startswith(
                ("demo://", "serpapi://")
            ):
                lines.append(f"Источник: {lead.source_url}")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def build_application(self) -> Application:
        application = (
            Application.builder().token(self.settings.telegram_bot_token).build()
        )
        application.add_handler(
            CallbackQueryHandler(
                self.select_star_payment,
                pattern=r"^buy:(?:standard|pro):(?:1|3|6|12)$",
            )
        )
        application.add_handler(PreCheckoutQueryHandler(self.precheckout))
        application.add_handler(
            MessageHandler(filters.SUCCESSFUL_PAYMENT, self.successful_payment)
        )

        navigation_commands = (
            "start",
            "menu",
            "status",
            "new_project",
            "projects",
            "find",
            "leads",
            "message",
            "analyze",
            "radars",
            "radar_run",
            "export",
            "analytics",
            "plans",
            "limits",
            "support",
            "help",
            "lead_status",
        )
        application.add_handler(
            ConversationHandler(
                entry_points=[
                    CommandHandler("find", self.find_start),
                    CommandHandler("new_project", self.new_project_start),
                    CommandHandler("message", self.message_start),
                    CommandHandler("analyze", self.analyze_start),
                    CommandHandler("radars", self.radar_start),
                    MessageHandler(
                        filters.Regex(_button_pattern(BUTTON_SEARCH)),
                        self.find_start,
                    ),
                    MessageHandler(
                        filters.Regex(_button_pattern(BUTTON_NEW_PROJECT)),
                        self.new_project_start,
                    ),
                    MessageHandler(
                        filters.Regex(_button_pattern(BUTTON_MESSAGE)),
                        self.message_start,
                    ),
                    MessageHandler(
                        filters.Regex(_button_pattern(BUTTON_ANALYZE)),
                        self.analyze_start,
                    ),
                    MessageHandler(
                        filters.Regex(_button_pattern(BUTTON_RADARS)),
                        self.radar_start,
                    ),
                ],
                states={
                    SEARCH_NICHE: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_niche)
                    ],
                    SEARCH_REGION: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_region)
                    ],
                    SEARCH_LIMIT: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_limit)
                    ],
                    PROJECT_NAME: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_project_name)
                    ],
                    PROJECT_NICHE: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_project_niche)
                    ],
                    PROJECT_REGION: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_project_region)
                    ],
                    MESSAGE_LEAD_ID: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_lead_id)
                    ],
                    ANALYZE_LEAD_ID: [
                        MessageHandler(
                            USER_INPUT_FILTER, self.receive_analyze_lead_id
                        )
                    ],
                    RADAR_NICHES: [
                        MessageHandler(
                            USER_INPUT_FILTER, self.receive_radar_niches
                        )
                    ],
                    RADAR_REGIONS: [
                        MessageHandler(
                            USER_INPUT_FILTER, self.receive_radar_regions
                        )
                    ],
                    RADAR_LIMIT: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_radar_limit)
                    ],
                },
                fallbacks=[
                    CommandHandler("cancel", self.cancel),
                    MessageHandler(
                        filters.Regex(MENU_BUTTON_PATTERN),
                        self.navigate_menu,
                    ),
                    CommandHandler(
                        navigation_commands,
                        self.navigate_command,
                    ),
                ],
                allow_reentry=True,
            )
        )

        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("menu", self.menu))
        application.add_handler(CommandHandler("status", self.status))
        application.add_handler(CommandHandler("projects", self.list_projects))
        application.add_handler(CommandHandler("leads", self.list_leads))
        application.add_handler(CommandHandler("export", self.export_leads))
        application.add_handler(CommandHandler("analytics", self.show_analytics))
        application.add_handler(CommandHandler("plans", self.show_plans))
        application.add_handler(CommandHandler("limits", self.show_limits))
        application.add_handler(CommandHandler("support", self.show_support))
        application.add_handler(CommandHandler("help", self.show_help))
        application.add_handler(CommandHandler("lead_status", self.lead_status))
        application.add_handler(CommandHandler("radar_run", self.radar_run))

        button_handlers = (
            (BUTTON_PROJECTS, self.list_projects),
            (BUTTON_LEADS, self.list_leads),
            (BUTTON_PIPELINE, self.show_pipeline),
            (BUTTON_EXPORT, self.export_leads),
            (BUTTON_ANALYTICS, self.show_analytics),
            (BUTTON_LIMITS, self.show_limits),
            (BUTTON_PLANS, self.show_plans),
            (BUTTON_SETTINGS, self.show_settings),
            (BUTTON_SUPPORT, self.show_support),
        )
        for button, handler in button_handlers:
            application.add_handler(
                MessageHandler(filters.Regex(_button_pattern(button)), handler)
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
