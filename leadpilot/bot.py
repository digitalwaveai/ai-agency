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
    SEARCH_PROJECT,
    SEARCH_SEGMENT,
    SEARCH_REGION,
    SEARCH_LIMIT,
    MESSAGE_LEAD_ID,
    PROJECT_CATEGORY,
    PROJECT_NAME,
    PROJECT_NICHE,
    PROJECT_OFFER,
    PROJECT_AUDIENCE,
    PROJECT_REGION,
    PROJECT_ADVANTAGE,
    ANALYZE_LEAD_ID,
    RADAR_NICHES,
    RADAR_REGIONS,
    RADAR_LIMIT,
) = range(16)

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

LIVE_STAR_TARIFFS = {
    ("standard", 1): ("Стандарт", 900),
    ("standard", 3): ("Стандарт", 2500),
    ("standard", 6): ("Стандарт", 4900),
    ("standard", 12): ("Стандарт", 9000),
    ("pro", 1): ("Pro", 2250),
    ("pro", 3): ("Pro", 6300),
    ("pro", 6): ("Pro", 12150),
    ("pro", 12): ("Pro", 22500),
}

TEST_STAR_TARIFFS = {
    ("standard", 1): ("Стандарт", 1),
    ("standard", 3): ("Стандарт", 2),
    ("standard", 6): ("Стандарт", 3),
    ("standard", 12): ("Стандарт", 4),
    ("pro", 1): ("Pro", 2),
    ("pro", 3): ("Pro", 4),
    ("pro", 6): ("Pro", 6),
    ("pro", 12): ("Pro", 10),
}

STAR_TARIFFS = LIVE_STAR_TARIFFS

PLAN_LIMITS = {
    "trial": (20, 20, 20, 20, 0),
    "standard": (100, 100, 100, 100, 3),
    "pro": (500, 500, 500, 500, 10),
}

LIVE_TARIFFS_TEXT = (
    "⭐ Тарифы LeadPilot AI\n\n"
    "🎁 Пробный — 7 дней бесплатно\n"
    "20 поисков · 20 лидов · 20 анализов · 20 сообщений\n\n"
    "⭐ Стандарт\n"
    "100 поисков · 100 лидов · 100 анализов · 100 сообщений · 3 радара\n"
    "1 месяц — 990 ₽ или 900 ⭐\n"
    "3 месяца — 2 790 ₽ или 2 500 ⭐\n"
    "6 месяцев — 5 390 ₽ или 4 900 ⭐\n"
    "12 месяцев — 9 990 ₽ или 9 000 ⭐\n\n"
    "🚀 Pro\n"
    "500 поисков · 500 лидов · 500 анализов · 500 сообщений · 10 радаров\n"
    "1 месяц — 2 490 ₽ или 2 250 ⭐\n"
    "3 месяца — 6 990 ₽ или 6 300 ⭐\n"
    "6 месяцев — 13 490 ₽ или 12 150 ⭐\n"
    "12 месяцев — 24 990 ₽ или 22 500 ⭐\n\n"
    "Оплата разовая, без автопродления.\n"
    "Для оплаты звёздами выберите вариант ниже."
)

TEST_TARIFFS_TEXT = (
    "🧪 Тестовые цены владельца\n\n"
    "⭐ Стандарт\n"
    "1 месяц — 1 ⭐ · 3 месяца — 2 ⭐\n"
    "6 месяцев — 3 ⭐ · 12 месяцев — 4 ⭐\n\n"
    "🚀 Pro\n"
    "1 месяц — 2 ⭐ · 3 месяца — 4 ⭐\n"
    "6 месяцев — 6 ⭐ · 12 месяцев — 10 ⭐\n\n"
    "Этот режим видите только вы. Для остальных пользователей всегда "
    "действуют реальные цены."
)

PROJECT_CATEGORIES = (
    ("beauty", "💅 Beauty и здоровье"),
    ("finance", "📈 Финансы и образование"),
    ("marketing", "📣 Маркетинг и продажи"),
    ("ai_automation", "🤖 AI и автоматизация"),
    ("development", "💻 Разработка и IT"),
    ("design", "🎨 Дизайн и визуал"),
    ("content", "🎬 Контент и видео"),
    ("ecommerce", "🛒 Маркетплейсы и e-commerce"),
    ("education_consulting", "🎓 Образование и консалтинг"),
    ("business_services", "🧾 Бизнес-услуги"),
    ("local_business", "📍 Локальный бизнес"),
    ("property_construction", "🏗️ Недвижимость и строительство"),
    ("custom", "🧩 Своя ниша"),
)
PROJECT_CATEGORY_MAP = dict(PROJECT_CATEGORIES)
PROJECT_CATEGORY_EXAMPLES = {
    "beauty": {
        "name": "AI-автоматизация для бьюти-экспертов",
        "niche": "косметологи и владельцы салонов",
        "offer": "AI-бот для записи и обработки заявок",
        "audience": "частный косметолог с входящими заявками",
        "advantage": "не терять обращения и быстрее отвечать клиентам",
    },
    "finance": {
        "name": "Автоматизация для онлайн-школ",
        "niche": "финансовые эксперты и онлайн-школы",
        "offer": "воронка консультаций с AI-ассистентом",
        "audience": "эксперт или школа с регулярными запусками",
        "advantage": "быстрее обрабатывать заявки и доводить их до консультации",
    },
    "marketing": {
        "name": "AI-продажи для агентств",
        "niche": "маркетинговые агентства и отделы продаж",
        "offer": "AI-система квалификации и прогрева лидов",
        "audience": "агентство с потоком входящих и холодных заявок",
        "advantage": "сократить ручную квалификацию и ускорить первый контакт",
    },
    "ai_automation": {
        "name": "AI-автоматизация заявок",
        "niche": "компании, которым нужна автоматизация процессов",
        "offer": "AI-ассистент для заявок и клиентского сервиса",
        "audience": "владелец бизнеса с повторяющимися ручными задачами",
        "advantage": "снизить нагрузку на команду и не терять обращения",
    },
    "development": {
        "name": "Разработка для растущего бизнеса",
        "niche": "компании без удобного сайта или внутренней системы",
        "offer": "разработка сайта, бота или веб-сервиса",
        "audience": "бизнес, которому нужен новый цифровой продукт",
        "advantage": "быстрее запустить продукт и автоматизировать работу",
    },
    "design": {
        "name": "Дизайн для экспертов",
        "niche": "эксперты и бренды с устаревшим визуалом",
        "offer": "фирменный стиль и дизайн материалов",
        "audience": "эксперт или компания перед запуском нового продукта",
        "advantage": "выглядеть профессионально и повысить доверие аудитории",
    },
    "content": {
        "name": "Контент для личных брендов",
        "niche": "эксперты и компании, которым нужен видеоконтент",
        "offer": "контент-стратегия и производство коротких видео",
        "audience": "эксперт, который регулярно продаёт через соцсети",
        "advantage": "выпускать контент стабильно и получать больше обращений",
    },
    "ecommerce": {
        "name": "Рост продаж на маркетплейсах",
        "niche": "продавцы на маркетплейсах и интернет-магазины",
        "offer": "аналитика карточек и автоматизация продаж",
        "audience": "селлер с действующими товарами и планом роста",
        "advantage": "повысить конверсию карточек и сократить ручную работу",
    },
    "education_consulting": {
        "name": "Воронка для экспертов",
        "niche": "консультанты, наставники и образовательные проекты",
        "offer": "система привлечения и записи на консультации",
        "audience": "эксперт с готовой услугой или программой",
        "advantage": "получать больше целевых заявок и быстрее их обрабатывать",
    },
    "business_services": {
        "name": "Автоматизация бизнес-услуг",
        "niche": "бухгалтерские, юридические и кадровые компании",
        "offer": "автоматизация первичной консультации и заявок",
        "audience": "сервисная компания с повторяющимися запросами клиентов",
        "advantage": "сократить время ответа и разгрузить специалистов",
    },
    "local_business": {
        "name": "Заявки для локального бизнеса",
        "niche": "клиники, студии и локальные сервисы",
        "offer": "AI-бот для записи и возврата клиентов",
        "audience": "локальный бизнес с записью по телефону и в мессенджерах",
        "advantage": "не пропускать заявки и заполнять свободные окна",
    },
    "property_construction": {
        "name": "AI-продажи в недвижимости",
        "niche": "агентства недвижимости и строительные компании",
        "offer": "квалификация обращений и запись на просмотр",
        "audience": "компания с потоком заявок на объекты",
        "advantage": "быстрее отвечать покупателям и отсеивать нецелевые заявки",
    },
    "custom": {
        "name": "Мой новый проект",
        "niche": "конкретная специализация или тип бизнеса",
        "offer": "ваш основной продукт или услуга",
        "audience": "клиент, которому особенно полезно ваше предложение",
        "advantage": "главная проблема, которую вы решаете для клиента",
    },
}

ROLE_LABELS = {
    "owner": "Владелец",
    "admin": "Администратор",
    "beta_tester": "Бета-тестер",
    "user": "Пользователь",
}


def _star_tariffs(mode: str) -> dict[tuple[str, int], tuple[str, int]]:
    return TEST_STAR_TARIFFS if mode == "test" else LIVE_STAR_TARIFFS


def _star_payment_keyboard(mode: str) -> InlineKeyboardMarkup:
    tariffs = _star_tariffs(mode)
    rows = []
    for plan_code in ("standard", "pro"):
        for months in (1, 3, 6, 12):
            plan_name, stars = tariffs[(plan_code, months)]
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{plan_name} {months} мес. · {stars:,} ⭐".replace(",", " "),
                        callback_data=f"buy:{plan_code}:{months}:{mode}",
                    )
                ]
            )
    return InlineKeyboardMarkup(rows)


def _owner_price_mode_keyboard(mode: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    ("✅ " if mode == "test" else "") + "🧪 Тестовые цены",
                    callback_data="price_mode:test",
                )
            ],
            [
                InlineKeyboardButton(
                    ("✅ " if mode == "live" else "") + "💳 Реальные цены",
                    callback_data="price_mode:live",
                )
            ],
        ]
    )


def _project_categories_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                label,
                callback_data=f"project_category:{code}",
            )
        ]
        for code, label in PROJECT_CATEGORIES
    ]
    rows.append(
        [InlineKeyboardButton("❌ Отмена", callback_data="project_category:cancel")]
    )
    return InlineKeyboardMarkup(rows)


def _project_search_keyboard(projects: list[dict[str, object]]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"📁 {project['name']}",
                callback_data=f"search_project:{project['id']}",
            )
        ]
        for project in projects
    ]
    rows.append(
        [InlineKeyboardButton("❌ Отмена", callback_data="search_project:cancel")]
    )
    return InlineKeyboardMarkup(rows)


SEARCH_LIMIT_KEYBOARD = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("5 клиентов", callback_data="search_limit:5"),
            InlineKeyboardButton("10 клиентов", callback_data="search_limit:10"),
        ],
        [InlineKeyboardButton("20 клиентов", callback_data="search_limit:20")],
        [InlineKeyboardButton("❌ Отмена", callback_data="search_limit:cancel")],
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
        if self.settings.owner_telegram_id == user.id:
            self.db.ensure_owner(user.id)

    def is_owner(self, update: Update) -> bool:
        owner_id = self.settings.owner_telegram_id
        user = update.effective_user
        return bool(user and owner_id is not None and user.id == owner_id)

    def role(self, update: Update) -> str:
        user = update.effective_user
        if not user:
            return "user"
        if self.is_owner(update):
            return "owner"
        return self.db.get_role(user.id)

    def is_unlimited(self, update: Update) -> bool:
        return self.role(update) in {"owner", "admin", "beta_tester"}

    def price_mode_for(self, user_id: int) -> str:
        if self.settings.owner_telegram_id != user_id:
            return "live"
        return self.db.get_price_mode(user_id)

    def payment_mode_allowed(self, user_id: int, mode: str) -> bool:
        return mode in {"live", "test"} and mode == self.price_mode_for(user_id)

    def authorized(self, update: Update) -> bool:
        user = update.effective_user
        if not user:
            return False
        self.ensure_account(update)
        if self.is_unlimited(update):
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
        role = self.role(update)
        access = self.db.get_access_state(user.id)
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
        await update.effective_message.reply_text("Главное меню:", reply_markup=MENU)

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
        projects = await asyncio.to_thread(
            self.db.list_projects, update.effective_user.id, 20
        )
        if not projects:
            await update.effective_message.reply_text(
                "🔎 Найти клиентов\n\n"
                "Сначала создайте проект — поиск запускается только из "
                "сохранённого проекта.",
                reply_markup=MENU,
            )
            return ConversationHandler.END
        await update.effective_message.reply_text(
            "🔎 Найти клиентов\n\nИз какого проекта найти клиентов?",
            reply_markup=_project_search_keyboard(projects),
        )
        return SEARCH_PROJECT

    async def select_search_project(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        query = update.callback_query
        if not query or not query.from_user:
            return ConversationHandler.END
        await query.answer()
        if query.data == "search_project:cancel":
            context.user_data.clear()
            await query.message.reply_text("Поиск отменён.", reply_markup=MENU)
            return ConversationHandler.END
        try:
            project_id = int((query.data or "").partition(":")[2])
        except ValueError:
            await query.message.reply_text(
                "Не удалось выбрать проект. Откройте поиск ещё раз.",
                reply_markup=MENU,
            )
            return ConversationHandler.END
        project = await asyncio.to_thread(
            self.db.get_project, query.from_user.id, project_id
        )
        if not project:
            await query.message.reply_text(
                "Проект не найден или недоступен.", reply_markup=MENU
            )
            return ConversationHandler.END
        context.user_data["search_project"] = project
        default_segment = str(
            project.get("target_audience") or project.get("niche") or ""
        )
        await query.message.reply_text(
            f"📁 Проект: {project['name']}\n\n"
            "1/3. Каких клиентов ищем сейчас?\n"
            f"По умолчанию: {default_segment}\n\n"
            "Напишите уточнение или отправьте «-», чтобы оставить вариант "
            "проекта.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return SEARCH_SEGMENT

    async def receive_search_segment(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        value = (update.effective_message.text or "").strip()
        project = dict(context.user_data["search_project"])
        default_segment = str(
            project.get("target_audience") or project.get("niche") or ""
        ).strip()
        segment = default_segment if value == "-" else value
        if len(segment) < 2:
            await update.effective_message.reply_text(
                "Уточните тип клиентов минимум двумя символами или отправьте «-»."
            )
            return SEARCH_SEGMENT
        context.user_data["search_segment"] = segment
        default_region = str(project.get("region") or "").strip()
        await update.effective_message.reply_text(
            "2/3. В каком городе или регионе искать?\n"
            f"По умолчанию: {default_region or 'не указан'}\n\n"
            "Напишите регион или отправьте «-», чтобы оставить вариант проекта."
        )
        return SEARCH_REGION

    async def receive_search_region(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        value = (update.effective_message.text or "").strip()
        project = dict(context.user_data["search_project"])
        default_region = str(project.get("region") or "").strip()
        region = default_region if value == "-" else value
        if len(region) < 2:
            await update.effective_message.reply_text(
                "Укажите город или регион минимум двумя символами."
            )
            return SEARCH_REGION
        context.user_data["search_region"] = region
        await update.effective_message.reply_text(
            "3/3. Сколько клиентов показать?",
            reply_markup=SEARCH_LIMIT_KEYBOARD,
        )
        return SEARCH_LIMIT

    async def select_search_limit(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        query = update.callback_query
        if not query or not query.from_user:
            return ConversationHandler.END
        await query.answer()
        if query.data == "search_limit:cancel":
            context.user_data.clear()
            await query.message.reply_text("Поиск отменён.", reply_markup=MENU)
            return ConversationHandler.END
        try:
            limit = int((query.data or "").partition(":")[2])
        except ValueError:
            await query.message.reply_text(
                "Не удалось определить количество. Запустите поиск ещё раз.",
                reply_markup=MENU,
            )
            return ConversationHandler.END
        project = dict(context.user_data["search_project"])
        segment = str(context.user_data["search_segment"])
        region = str(context.user_data["search_region"])
        query_parts = [segment]
        for part in (
            str(project.get("niche") or "").strip(),
            str(project.get("offer") or "").strip(),
        ):
            if part and part.lower() not in segment.lower():
                query_parts.append(part)
        niche = " ".join(query_parts)
        await query.message.reply_text(
            f"Ищу для проекта «{project['name']}»: {segment}, {region}. "
            "Это может занять несколько секунд…"
        )
        await self._search_and_reply(
            update,
            niche,
            region,
            limit,
            project_id=int(project["id"]),
        )
        context.user_data.clear()
        return ConversationHandler.END

    async def _search_and_reply(
        self,
        update: Update,
        niche: str,
        region: str,
        limit: int,
        *,
        project_id: int | None = None,
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
            ids = await asyncio.to_thread(
                self.db.save_leads,
                user_id,
                leads,
                project_id,
            )
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
            "Выберите направление. У каждой ниши — своя анкета, боли, "
            "офферы и правила поиска.",
            reply_markup=_project_categories_keyboard(),
        )
        return PROJECT_CATEGORY

    async def select_project_category(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        query = update.callback_query
        if not query or not query.from_user:
            return ConversationHandler.END
        await query.answer()
        code = (query.data or "").partition(":")[2]
        if code == "cancel":
            context.user_data.clear()
            await query.message.reply_text(
                "Создание проекта отменено.", reply_markup=MENU
            )
            return ConversationHandler.END
        label = PROJECT_CATEGORY_MAP.get(code)
        if not label:
            await query.message.reply_text(
                "Направление не найдено. Нажмите «➕ Новый проект» ещё раз.",
                reply_markup=MENU,
            )
            return ConversationHandler.END
        context.user_data["project_category_code"] = code
        context.user_data["project_category_name"] = label.split(" ", 1)[1]
        context.user_data["project_category_examples"] = PROJECT_CATEGORY_EXAMPLES[code]
        examples = PROJECT_CATEGORY_EXAMPLES[code]
        await query.message.reply_text(
            f"{label}\n\n"
            "1/6. Как назвать проект?\n"
            f"Например: {examples['name']}",
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
        examples = context.user_data["project_category_examples"]
        await update.effective_message.reply_text(
            "2/6. Уточните нишу или специализацию проекта.\n"
            f"Например: {examples['niche']}"
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
        examples = context.user_data["project_category_examples"]
        await update.effective_message.reply_text(
            "3/6. Что вы предлагаете этим клиентам?\n"
            f"Например: {examples['offer']}"
        )
        return PROJECT_OFFER

    async def receive_project_offer(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        offer = (update.effective_message.text or "").strip()
        if len(offer) < 3:
            await update.effective_message.reply_text(
                "Опишите услугу или продукт минимум тремя символами."
            )
            return PROJECT_OFFER
        context.user_data["project_offer"] = offer
        examples = context.user_data["project_category_examples"]
        await update.effective_message.reply_text(
            "4/6. Кто ваш идеальный клиент?\n"
            f"Например: {examples['audience']}"
        )
        return PROJECT_AUDIENCE

    async def receive_project_audience(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        audience = (update.effective_message.text or "").strip()
        if len(audience) < 3:
            await update.effective_message.reply_text(
                "Опишите целевого клиента минимум тремя символами."
            )
            return PROJECT_AUDIENCE
        context.user_data["project_audience"] = audience
        await update.effective_message.reply_text(
            "5/6. Основной город или регион проекта?\nНапример: Москва или вся Россия"
        )
        return PROJECT_REGION

    async def receive_project_region(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        region = (update.effective_message.text or "").strip()
        if len(region) < 2:
            await update.effective_message.reply_text("Укажите регион точнее.")
            return PROJECT_REGION
        context.user_data["project_region"] = region
        examples = context.user_data["project_category_examples"]
        await update.effective_message.reply_text(
            "6/6. Какую главную проблему клиента решает ваш продукт?\n"
            f"Например: {examples['advantage']}"
        )
        return PROJECT_ADVANTAGE

    async def receive_project_advantage(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        advantage = (update.effective_message.text or "").strip()
        if len(advantage) < 3:
            await update.effective_message.reply_text(
                "Уточните пользу или решаемую проблему минимум тремя символами."
            )
            return PROJECT_ADVANTAGE
        await asyncio.to_thread(
            self.db.create_project,
            update.effective_user.id,
            str(context.user_data["project_name"]),
            str(context.user_data["project_niche"]),
            str(context.user_data["project_region"]),
            category_code=str(context.user_data["project_category_code"]),
            category_name=str(context.user_data["project_category_name"]),
            offer=str(context.user_data["project_offer"]),
            target_audience=str(context.user_data["project_audience"]),
            advantage=advantage,
        )
        await update.effective_message.reply_text(
            "✅ Проект активирован\n\n"
            f"📁 {context.user_data['project_name']}\n\n"
            f"Направление: {context.user_data['project_category_name']}\n"
            f"Ниша: {context.user_data['project_niche']}\n"
            "Статус: ✅ Активен\n"
            "Анкета: 6 / 6\n\n"
            "Проект сохранён. Клиенты пока не искались.\n"
            "Нажмите «🔎 Найти клиентов», выберите этот проект и ответьте "
            "на 3 коротких вопроса.",
            reply_markup=MENU,
        )
        context.user_data.clear()
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
            answered = sum(
                bool(str(project.get(field) or "").strip())
                for field in (
                    "name",
                    "niche",
                    "offer",
                    "target_audience",
                    "region",
                    "advantage",
                )
            )
            text.append(
                f"📁 {project['name']}\n"
                f"Направление: {project['category_name']}\n"
                f"Ниша: {project['niche']}\n"
                f"Статус: ✅ Активен\n"
                f"Анкета: {answered} / 6\n"
                f"ID проекта: {project['id']}"
            )
        await update.effective_message.reply_text("\n\n".join(text), reply_markup=MENU)

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
            self.format_leads(leads) + "\n\nВведите ID клиента для подробного анализа.",
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

    async def _lead_from_message(self, update: Update, state: int) -> Lead | None:
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
            await update.effective_message.reply_text("Введите хотя бы одну нишу.")
            return RADAR_NICHES
        context.user_data["radar_niches"] = niches[:3]
        await update.effective_message.reply_text(
            "Введите города или регионы через запятую.\nПример: Москва, Казань"
        )
        return RADAR_REGIONS

    async def receive_radar_regions(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        regions = self._split_values(update.effective_message.text)
        if not regions:
            await update.effective_message.reply_text("Введите хотя бы один регион.")
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
        combinations = [(niche, region) for niche in niches for region in regions][:6]
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
        if self.is_unlimited(update):
            role = self.role(update)
            await update.effective_message.reply_text(
                "📊 Ваши лимиты\n\n"
                f"Роль: {ROLE_LABELS[role]}\n"
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

        searches, leads, analyses, messages, radars = PLAN_LIMITS[access["plan_code"]]
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
        mode = self.price_mode_for(update.effective_user.id)
        text = TEST_TARIFFS_TEXT if mode == "test" else LIVE_TARIFFS_TEXT
        await update.effective_message.reply_text(text, reply_markup=MENU)
        await update.effective_message.reply_text(
            "Выберите тариф и срок для оплаты Telegram Stars:",
            reply_markup=_star_payment_keyboard(mode),
        )

    async def price_mode(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        self.ensure_account(update)
        if not self.is_owner(update):
            await update.effective_message.reply_text(
                "Команда недоступна.", reply_markup=MENU
            )
            return
        owner_id = update.effective_user.id
        requested = context.args[0].lower() if context.args else ""
        aliases = {
            "test": "test",
            "тест": "test",
            "тестовые": "test",
            "live": "live",
            "real": "live",
            "реальные": "live",
        }
        if requested:
            mode = aliases.get(requested)
            if mode is None:
                await update.effective_message.reply_text(
                    "Формат команды:\n"
                    "/price_mode test — тестовые цены\n"
                    "/price_mode live — реальные цены",
                    reply_markup=MENU,
                )
                return
            if not self.db.set_owner_price_mode(owner_id, mode):
                await update.effective_message.reply_text(
                    "Не удалось изменить режим цен.", reply_markup=MENU
                )
                return
            label = "тестовые" if mode == "test" else "реальные"
            await update.effective_message.reply_text(
                "✅ Режим цен переключён\n\n"
                f"Теперь для ваших новых счетов действуют {label} цены.\n"
                "Для всех остальных пользователей всегда действуют реальные цены.",
                reply_markup=MENU,
            )
            return

        mode = self.db.get_price_mode(owner_id)
        label = "тестовые" if mode == "test" else "реальные"
        await update.effective_message.reply_text(
            "💳 Режим цен владельца\n\n"
            f"Сейчас включены: {label} цены.\n"
            "Какие цены использовать для ваших следующих счетов?",
            reply_markup=_owner_price_mode_keyboard(mode),
        )

    async def switch_price_mode(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        owner_id = self.settings.owner_telegram_id
        if not query or not query.from_user:
            return
        if owner_id is None or query.from_user.id != owner_id:
            await query.answer("Доступно только владельцу.", show_alert=True)
            return
        await query.answer()
        mode = (query.data or "").partition(":")[2]
        if mode not in {"test", "live"}:
            return
        self.db.ensure_account(
            query.from_user.id,
            username=query.from_user.username or "",
            first_name=query.from_user.first_name or "",
        )
        self.db.ensure_owner(query.from_user.id)
        if not self.db.set_owner_price_mode(query.from_user.id, mode):
            await query.message.reply_text(
                "Не удалось изменить режим цен.", reply_markup=MENU
            )
            return
        label = "тестовые" if mode == "test" else "реальные"
        await query.message.edit_text(
            "✅ Режим цен переключён\n\n"
            f"Теперь для ваших новых счетов действуют {label} цены.\n"
            "Для всех остальных пользователей всегда действуют реальные цены.",
            reply_markup=_owner_price_mode_keyboard(mode),
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
            _, plan_code, months_raw, mode = (query.data or "").split(":")
            months = int(months_raw)
            if mode not in {"live", "test"}:
                raise ValueError
            if mode == "test" and query.from_user.id != self.settings.owner_telegram_id:
                raise PermissionError
            if mode != self.price_mode_for(query.from_user.id):
                raise RuntimeError
            plan_name, stars = _star_tariffs(mode)[(plan_code, months)]
        except PermissionError:
            await query.message.reply_text(
                "Тестовые цены доступны только владельцу.", reply_markup=MENU
            )
            return
        except RuntimeError:
            await query.message.reply_text(
                "Режим цен уже изменён. Откройте «⭐ Тарифы» ещё раз.",
                reply_markup=MENU,
            )
            return
        except (ValueError, KeyError):
            await query.message.reply_text(
                "Не удалось определить тариф. Откройте «⭐ Тарифы» ещё раз.",
                reply_markup=MENU,
            )
            return

        payload = f"leadpilot|{plan_code}|{months}|{query.from_user.id}|{mode}"
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
        plan_code, months, user_id, mode = parsed
        expected = _star_tariffs(mode).get((plan_code, months))
        valid = bool(
            expected
            and query.from_user.id == user_id
            and query.currency == "XTR"
            and query.total_amount == expected[1]
            and self.payment_mode_allowed(query.from_user.id, mode)
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
        plan_code, months, payload_user_id, mode = parsed
        expected = _star_tariffs(mode).get((plan_code, months))
        if (
            not expected
            or payload_user_id != user.id
            or payment.currency != "XTR"
            or payment.total_amount != expected[1]
            or not self.payment_mode_allowed(user.id, mode)
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

    async def show_role(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        self.ensure_account(update)
        role = self.role(update)
        access = (
            "без лимитов"
            if role in {"owner", "admin", "beta_tester"}
            else "по активному тарифу"
        )
        await update.effective_message.reply_text(
            f"👤 Роль аккаунта\n\nРоль: {ROLE_LABELS[role]}\nДоступ: {access}",
            reply_markup=MENU,
        )

    @staticmethod
    def _target_user_id(context: ContextTypes.DEFAULT_TYPE) -> int | None:
        if len(context.args) != 1:
            return None
        try:
            value = int(context.args[0])
        except ValueError:
            return None
        return value if value > 0 else None

    async def owner_grant_admin(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        self.ensure_account(update)
        if not self.is_owner(update):
            await update.effective_message.reply_text("Команда недоступна.")
            return
        target_id = self._target_user_id(context)
        if target_id is None:
            await update.effective_message.reply_text(
                "Формат: /owner_admin TELEGRAM_ID", reply_markup=MENU
            )
            return
        if target_id == update.effective_user.id:
            await update.effective_message.reply_text(
                "Владелец не может получить другую роль.", reply_markup=MENU
            )
            return
        await asyncio.to_thread(self.db.ensure_account, target_id)
        await asyncio.to_thread(self.db.set_role, target_id, "admin")
        await update.effective_message.reply_text(
            f"✅ Пользователь {target_id} получил роль «Администратор».\n"
            "У него бессрочный доступ без лимитов. Он может управлять только "
            "назначенными им бета-тестерами и пользователями.",
            reply_markup=MENU,
        )

    async def owner_revoke_admin(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        self.ensure_account(update)
        if not self.is_owner(update):
            await update.effective_message.reply_text("Команда недоступна.")
            return
        target_id = self._target_user_id(context)
        if target_id is None:
            await update.effective_message.reply_text(
                "Формат: /owner_revoke_admin TELEGRAM_ID", reply_markup=MENU
            )
            return
        record = await asyncio.to_thread(self.db.get_role_record, target_id)
        if record["role"] != "admin":
            await update.effective_message.reply_text(
                "У пользователя нет роли администратора.", reply_markup=MENU
            )
            return
        await asyncio.to_thread(self.db.set_role, target_id, "user")
        await update.effective_message.reply_text(
            f"✅ Пользователь {target_id} переведён в роль «Пользователь».",
            reply_markup=MENU,
        )

    async def admin_grant_beta(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        self.ensure_account(update)
        actor_role = self.role(update)
        if actor_role not in {"owner", "admin"}:
            await update.effective_message.reply_text("Команда недоступна.")
            return
        target_id = self._target_user_id(context)
        if target_id is None:
            await update.effective_message.reply_text(
                "Формат: /admin_beta TELEGRAM_ID", reply_markup=MENU
            )
            return
        if target_id == self.settings.owner_telegram_id:
            await update.effective_message.reply_text(
                "Роль владельца изменить нельзя.", reply_markup=MENU
            )
            return
        await asyncio.to_thread(self.db.ensure_account, target_id)
        record = await asyncio.to_thread(self.db.get_role_record, target_id)
        if record["role"] == "admin":
            await update.effective_message.reply_text(
                "Администратора нельзя изменить этой командой.",
                reply_markup=MENU,
            )
            return
        if (
            actor_role == "admin"
            and record["role"] == "beta_tester"
            and record["managed_by"] not in {None, update.effective_user.id}
        ):
            await update.effective_message.reply_text(
                "Этим бета-тестером управляет другой администратор.",
                reply_markup=MENU,
            )
            return
        await asyncio.to_thread(
            self.db.set_role,
            target_id,
            "beta_tester",
            managed_by=update.effective_user.id,
        )
        await update.effective_message.reply_text(
            f"✅ Пользователь {target_id} получил роль «Бета-тестер» "
            "и доступ без лимитов.",
            reply_markup=MENU,
        )

    async def admin_set_user(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        self.ensure_account(update)
        actor_role = self.role(update)
        if actor_role not in {"owner", "admin"}:
            await update.effective_message.reply_text("Команда недоступна.")
            return
        target_id = self._target_user_id(context)
        if target_id is None:
            await update.effective_message.reply_text(
                "Формат: /admin_user TELEGRAM_ID", reply_markup=MENU
            )
            return
        record = await asyncio.to_thread(self.db.get_role_record, target_id)
        if record["role"] == "owner":
            await update.effective_message.reply_text(
                "Роль владельца изменить нельзя.", reply_markup=MENU
            )
            return
        if actor_role == "admin" and (
            record["role"] != "beta_tester"
            or record["managed_by"] != update.effective_user.id
        ):
            await update.effective_message.reply_text(
                "Администратор может изменять только назначенных им бета-тестеров.",
                reply_markup=MENU,
            )
            return
        await asyncio.to_thread(self.db.set_role, target_id, "user")
        await update.effective_message.reply_text(
            f"✅ Пользователь {target_id} переведён в роль «Пользователь».",
            reply_markup=MENU,
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
            "/role — роль аккаунта\n"
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
            "price_mode": self.price_mode,
            "limits": self.show_limits,
            "support": self.show_support,
            "help": self.show_help,
            "lead_status": self.lead_status,
            "role": self.show_role,
            "owner_admin": self.owner_grant_admin,
            "owner_revoke_admin": self.owner_revoke_admin,
            "admin_beta": self.admin_grant_beta,
            "admin_user": self.admin_set_user,
        }
        handler = handlers.get(command)
        if not handler:
            return ConversationHandler.END
        result = await handler(update, context)
        return result if isinstance(result, int) else ConversationHandler.END

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data.clear()
        await update.effective_message.reply_text(
            "Действие отменено.", reply_markup=MENU
        )
        return ConversationHandler.END

    async def error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    def _parse_payment_payload(
        payload: str,
    ) -> tuple[str, int, int, str] | None:
        try:
            parts = payload.split("|")
            if len(parts) == 4:
                prefix, plan_code, months_raw, user_id_raw = parts
                mode = "live"
            elif len(parts) == 5:
                prefix, plan_code, months_raw, user_id_raw, mode = parts
            else:
                return None
            months = int(months_raw)
            user_id = int(user_id_raw)
        except (ValueError, AttributeError):
            return None
        if (
            prefix != "leadpilot"
            or mode not in {"live", "test"}
            or (plan_code, months) not in _star_tariffs(mode)
        ):
            return None
        return plan_code, months, user_id, mode

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
                self.switch_price_mode,
                pattern=r"^price_mode:(?:test|live)$",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self.select_star_payment,
                pattern=r"^buy:(?:standard|pro):(?:1|3|6|12):(?:live|test)$",
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
            "price_mode",
            "limits",
            "support",
            "help",
            "lead_status",
            "role",
            "owner_admin",
            "owner_revoke_admin",
            "admin_beta",
            "admin_user",
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
                    SEARCH_PROJECT: [
                        CallbackQueryHandler(
                            self.select_search_project,
                            pattern=r"^search_project:(?:\d+|cancel)$",
                        )
                    ],
                    SEARCH_SEGMENT: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_search_segment)
                    ],
                    SEARCH_REGION: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_search_region)
                    ],
                    SEARCH_LIMIT: [
                        CallbackQueryHandler(
                            self.select_search_limit,
                            pattern=r"^search_limit:(?:5|10|20|cancel)$",
                        )
                    ],
                    PROJECT_CATEGORY: [
                        CallbackQueryHandler(
                            self.select_project_category,
                            pattern=(
                                r"^project_category:(?:"
                                + "|".join(
                                    re.escape(code) for code, _ in PROJECT_CATEGORIES
                                )
                                + r"|cancel)$"
                            ),
                        )
                    ],
                    PROJECT_NAME: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_project_name)
                    ],
                    PROJECT_NICHE: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_project_niche)
                    ],
                    PROJECT_OFFER: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_project_offer)
                    ],
                    PROJECT_AUDIENCE: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_project_audience)
                    ],
                    PROJECT_REGION: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_project_region)
                    ],
                    PROJECT_ADVANTAGE: [
                        MessageHandler(
                            USER_INPUT_FILTER, self.receive_project_advantage
                        )
                    ],
                    MESSAGE_LEAD_ID: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_lead_id)
                    ],
                    ANALYZE_LEAD_ID: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_analyze_lead_id)
                    ],
                    RADAR_NICHES: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_radar_niches)
                    ],
                    RADAR_REGIONS: [
                        MessageHandler(USER_INPUT_FILTER, self.receive_radar_regions)
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
        application.add_handler(CommandHandler("price_mode", self.price_mode))
        application.add_handler(CommandHandler("limits", self.show_limits))
        application.add_handler(CommandHandler("support", self.show_support))
        application.add_handler(CommandHandler("help", self.show_help))
        application.add_handler(CommandHandler("lead_status", self.lead_status))
        application.add_handler(CommandHandler("radar_run", self.radar_run))
        application.add_handler(CommandHandler("role", self.show_role))
        application.add_handler(CommandHandler("owner_admin", self.owner_grant_admin))
        application.add_handler(
            CommandHandler("owner_revoke_admin", self.owner_revoke_admin)
        )
        application.add_handler(CommandHandler("admin_beta", self.admin_grant_beta))
        application.add_handler(CommandHandler("admin_user", self.admin_set_user))

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
    if settings.owner_telegram_id is not None:
        bot.db.ensure_owner(settings.owner_telegram_id)
    logging.info("LeadPilot starting in long-polling mode")
    bot.build_application().run_polling(drop_pending_updates=False)
