from __future__ import annotations

import asyncio
import re
from functools import wraps
from typing import Any

from telegram import ReplyKeyboardRemove, Update
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
)

from .bot import (
    MENU,
    PROJECT_ADVANTAGE,
    PROJECT_AUDIENCE,
    PROJECT_CATEGORY_MAP,
    PROJECT_NAME,
    PROJECT_NICHE,
    PROJECT_OFFER,
    PROJECT_REGION,
    USER_INPUT_FILTER,
)

PROJECT_PRIORITIES = 16
PROJECT_EXCLUSIONS = 17


QUESTIONNAIRES: dict[str, dict[str, str]] = {
    "beauty": {
        "name": "Как назвать бьюти-проект?\nНапример: AI-запись для косметологов",
        "niche": "Какой именно бьюти-сегмент вы обслуживаете?\nНапример: косметологи, салоны красоты или массажные студии",
        "offer": "Что именно вы предлагаете этому сегменту?\nНапример: AI-бот для записи и обработки обращений",
        "audience": "Кого конкретно искать среди бьюти-бизнесов?\nНапример: частный косметолог с активной записью через мессенджеры",
        "region": "В каком городе или регионе искать бьюти-клиентов?\nНапример: Воронеж или вся Россия",
        "advantage": "Какую главную проблему бьюти-клиента решает продукт?\nНапример: не терять заявки и заполнять свободные окна",
        "priorities": "Какие бьюти-клиенты или заказы для вас в приоритете?\nНапример: салоны с активными соцсетями, но без онлайн-записи",
        "exclusions": "Кого точно не нужно искать?\nНапример: крупные сети, франшизы и клиники с собственной сильной CRM",
    },
    "finance": {
        "name": "Как назвать проект в финансах или образовании?\nНапример: AI-воронка для финансовых экспертов",
        "niche": "Какое точное направление вы обслуживаете?\nНапример: финансовые консультанты, школы инвестиций или бухгалтерские курсы",
        "offer": "Что вы предлагаете таким проектам?\nНапример: AI-ассистент для консультаций и квалификации заявок",
        "audience": "Кого именно искать?\nНапример: эксперт или онлайн-школа с регулярными запусками",
        "region": "Где искать клиентов?\nНапример: Москва, вся Россия или русскоязычный рынок",
        "advantage": "Какую бизнес-проблему вы решаете?\nНапример: быстрее обрабатывать заявки и доводить их до консультации",
        "priorities": "Какие проекты для вас наиболее интересны?\nНапример: школы с действующим продуктом и стабильным потоком лидов",
        "exclusions": "Кого не нужно включать в поиск?\nНапример: банки, государственные организации и проекты без действующего продукта",
    },
    "marketing": {
        "name": "Как назвать маркетинговый проект?\nНапример: AI-продажи для агентств",
        "niche": "Какую специализацию маркетинга вы закрываете?\nНапример: digital-агентства, отделы продаж или продюсерские центры",
        "offer": "Что конкретно вы предлагаете?\nНапример: AI-квалификацию и автоматический прогрев лидов",
        "audience": "Какие компании искать?\nНапример: агентства с потоком входящих и холодных заявок",
        "region": "В каком регионе искать компании?\nНапример: Россия или СНГ",
        "advantage": "Какой результат получает клиент?\nНапример: меньше ручной квалификации и более быстрый первый контакт",
        "priorities": "Какие агентства или отделы продаж в приоритете?\nНапример: команды от 3 человек с действующей рекламой",
        "exclusions": "Кого исключить?\nНапример: фрилансеров без команды, вакансии и каталоги агентств",
    },
    "ai_automation": {
        "name": "Как назвать AI-проект?\nНапример: AI-автоматизация заявок",
        "niche": "Для какого типа бизнеса вы делаете автоматизацию?\nНапример: клиники, агентства, школы или сервисные компании",
        "offer": "Какой процесс вы автоматизируете?\nНапример: первичную консультацию, обработку заявок или поддержку клиентов",
        "audience": "Кого именно нужно искать?\nНапример: владельцы бизнеса с повторяющимися ручными задачами",
        "region": "Где искать такие компании?\nНапример: Воронеж, Россия или весь русскоязычный рынок",
        "advantage": "Какую проблему автоматизация решает в первую очередь?\nНапример: снижает нагрузку на команду и не даёт терять обращения",
        "priorities": "Какие процессы и компании для вас в приоритете?\nНапример: бизнес с большим количеством заявок в Telegram и WhatsApp",
        "exclusions": "Кого не искать?\nНапример: разработчиков AI, каталоги нейросетей, новости и компании с готовой сильной автоматизацией",
    },
    "development": {
        "name": "Как назвать проект по разработке?\nНапример: Веб-сервисы для растущего бизнеса",
        "niche": "Для какого бизнеса вы разрабатываете решения?\nНапример: локальные сервисы, интернет-магазины или образовательные проекты",
        "offer": "Что именно вы разрабатываете?\nНапример: сайты, Telegram-боты или внутренние веб-системы",
        "audience": "Какие компании искать?\nНапример: бизнес без удобного сайта или с большим количеством ручной работы",
        "region": "В каком регионе искать клиентов?\nНапример: Россия или СНГ",
        "advantage": "Какую проблему клиента решает разработка?\nНапример: быстрее запустить продукт и автоматизировать операции",
        "priorities": "Какие проекты для вас приоритетны?\nНапример: Telegram-боты, кабинеты клиентов или корпоративные сайты",
        "exclusions": "Кого не искать?\nНапример: вакансии разработчиков, IT-компании и проекты без бюджета на запуск",
    },
    "design": {
        "name": "Как назвать дизайн-проект?\nНапример: Инфографика для маркетплейсов",
        "niche": "Какой именно дизайн вы делаете?\nНапример: инфографика карточек товаров, rich-контент, брендинг или веб-дизайн",
        "offer": "Что получает клиент?\nНапример: комплект продающей инфографики для карточки товара",
        "audience": "Кому нужен этот дизайн?\nНапример: продавцы и бренды на Wildberries и Ozon",
        "region": "Где искать клиентов?\nНапример: вся Россия или русскоязычные селлеры",
        "advantage": "Какую задачу клиента решает дизайн?\nНапример: повышает кликабельность и конверсию карточки товара",
        "priorities": "Какие дизайн-заказы для вас в приоритете?\nНапример: карточки товаров для действующих селлеров с несколькими SKU",
        "exclusions": "Кого точно не искать?\nНапример: казино, вакансии, других дизайнеров, курсы дизайна и статьи об инфографике",
    },
    "content": {
        "name": "Как назвать контент-проект?\nНапример: Reels для личных брендов",
        "niche": "Какой контент вы создаёте?\nНапример: короткие видео, сценарии, съёмка или ведение социальных сетей",
        "offer": "Как выглядит ваша услуга?\nНапример: контент-стратегия и 20 коротких видео в месяц",
        "audience": "Для кого вы создаёте контент?\nНапример: эксперты и компании, которые продают через соцсети",
        "region": "Где искать клиентов?\nНапример: Воронеж для съёмки или вся Россия для удалённой работы",
        "advantage": "Какой результат получает клиент?\nНапример: регулярный контент и больше входящих обращений",
        "priorities": "Какие форматы или клиенты для вас в приоритете?\nНапример: эксперты с готовым продуктом и активным личным брендом",
        "exclusions": "Кого исключить?\nНапример: вакансии, видеостудии-конкуренты и проекты без активных соцсетей",
    },
    "ecommerce": {
        "name": "Как назвать проект для e-commerce?\nНапример: Рост продаж на маркетплейсах",
        "niche": "С каким типом продавцов вы работаете?\nНапример: селлеры Wildberries, Ozon или интернет-магазины",
        "offer": "Что вы предлагаете продавцам?\nНапример: аналитику карточек, управление рекламой или автоматизацию продаж",
        "audience": "Каких продавцов искать?\nНапример: действующие селлеры с несколькими товарами и планом роста",
        "region": "На каком рынке искать клиентов?\nНапример: Россия или СНГ",
        "advantage": "Какую проблему продавца вы решаете?\nНапример: повышаете конверсию и сокращаете ручную работу",
        "priorities": "Какие категории товаров или продавцы в приоритете?\nНапример: бренды одежды и косметики с оборотом и активной рекламой",
        "exclusions": "Кого не искать?\nНапример: пункты выдачи, вакансии, курсы для селлеров и агентства-конкуренты",
    },
    "education_consulting": {
        "name": "Как назвать проект для экспертов?\nНапример: Воронка консультаций",
        "niche": "С какими экспертами или образовательными проектами вы работаете?\nНапример: наставники, консультанты или онлайн-школы",
        "offer": "Что вы им предлагаете?\nНапример: систему привлечения и записи на консультации",
        "audience": "Кого именно искать?\nНапример: эксперт с готовой услугой, программой и активной аудиторией",
        "region": "На каком рынке искать?\nНапример: вся Россия или русскоязычный рынок",
        "advantage": "Какую проблему эксперта вы решаете?\nНапример: больше целевых заявок и меньше ручной переписки",
        "priorities": "Какие эксперты или школы для вас в приоритете?\nНапример: проекты с регулярными запусками и средним чеком от консультации",
        "exclusions": "Кого исключить?\nНапример: государственные учреждения, бесплатные сообщества и проекты без продукта",
    },
    "business_services": {
        "name": "Как назвать проект для бизнес-услуг?\nНапример: Автоматизация юридических заявок",
        "niche": "Какой вид бизнес-услуг вы обслуживаете?\nНапример: юридические, бухгалтерские или кадровые компании",
        "offer": "Что вы им предлагаете?\nНапример: автоматизацию первичной консультации и сбора данных",
        "audience": "Какие компании искать?\nНапример: небольшие фирмы с постоянным потоком однотипных обращений",
        "region": "В каком регионе искать?\nНапример: Воронежская область или вся Россия",
        "advantage": "Какую проблему вы решаете?\nНапример: сокращаете время ответа и разгружаете специалистов",
        "priorities": "Какие компании или обращения в приоритете?\nНапример: фирмы с активной рекламой и консультациями через мессенджеры",
        "exclusions": "Кого не искать?\nНапример: государственные органы, вакансии и крупные федеральные сети",
    },
    "local_business": {
        "name": "Как назвать проект для локального бизнеса?\nНапример: AI-запись для местных студий",
        "niche": "Какой локальный бизнес вам нужен?\nНапример: клиники, автосервисы, студии или ремонтные компании",
        "offer": "Что вы предлагаете такому бизнесу?\nНапример: AI-бот для записи, консультации и возврата клиентов",
        "audience": "Какие заведения искать?\nНапример: бизнес с записью по телефону и в мессенджерах",
        "region": "В каком городе или районе искать?\nНапример: Воронеж и ближайшие районы",
        "advantage": "Какую локальную проблему вы решаете?\nНапример: не пропускать заявки и заполнять свободные окна",
        "priorities": "Какие заведения для вас в приоритете?\nНапример: компании с хорошими отзывами, но без онлайн-записи",
        "exclusions": "Кого не искать?\nНапример: закрытые компании, федеральные сети и организации без контактов",
    },
    "property_construction": {
        "name": "Как назвать проект в недвижимости?\nНапример: AI-квалификация покупателей",
        "niche": "Какой сегмент недвижимости или строительства вам нужен?\nНапример: агентства недвижимости, застройщики или ремонтные компании",
        "offer": "Что вы предлагаете?\nНапример: AI-квалификацию обращений и запись на просмотр",
        "audience": "Какие компании искать?\nНапример: агентства с активной рекламой объектов и потоком обращений",
        "region": "В каком городе или регионе искать?\nНапример: Воронежская область",
        "advantage": "Какую проблему вы решаете?\nНапример: быстрый ответ покупателю и отсев нецелевых заявок",
        "priorities": "Какие объекты или компании в приоритете?\nНапример: новостройки, загородная недвижимость или агентства от 5 сотрудников",
        "exclusions": "Кого исключить?\nНапример: доски объявлений, частные объявления, вакансии и закрытые компании",
    },
    "custom": {
        "name": "Как назвать проект?\nНапример: Название вашей услуги и целевой ниши",
        "niche": "Опишите точную специализацию проекта.\nНапример: что именно вы делаете и для какой сферы",
        "offer": "Что конкретно вы предлагаете клиенту?\nНапример: основной продукт, услуга или пакет работ",
        "audience": "Кого именно нужно искать?\nНапример: тип компании, должность или характеристики идеального клиента",
        "region": "В каком городе, регионе или рынке искать?\nНапример: Воронеж, Россия или весь онлайн-рынок",
        "advantage": "Какую главную проблему клиента вы решаете?\nНапример: какой измеримый результат он получает",
        "priorities": "Какие клиенты, проекты или заказы для вас в приоритете?\nНапример: размер бизнеса, бюджет, активность или конкретный тип задачи",
        "exclusions": "Кого или что точно не нужно включать в поиск?\nНапример: конкуренты, вакансии, каталоги, крупные сети или запрещённые тематики",
    },
}


def _profile(code: str) -> dict[str, str]:
    return QUESTIONNAIRES.get(code, QUESTIONNAIRES["custom"])


def _question(number: int, text: str) -> str:
    return f"{number}/8. {text}"


def install_project_questionnaires(bot_class: type[Any], database_class: type[Any]) -> None:
    """Install category-specific eight-question project questionnaires."""
    if getattr(bot_class, "_project_questionnaires_installed", False):
        return

    old_init_schema = database_class.init_schema

    @wraps(old_init_schema)
    def init_schema(self: Any) -> None:
        old_init_schema(self)
        with self._connect() as connection:
            if self.is_postgres:
                connection.execute(
                    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS "
                    "priorities TEXT NOT NULL DEFAULT ''"
                )
                connection.execute(
                    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS "
                    "exclusions TEXT NOT NULL DEFAULT ''"
                )
            else:
                columns = {
                    str(row["name"])
                    for row in connection.execute(
                        "PRAGMA table_info(projects)"
                    ).fetchall()
                }
                if "priorities" not in columns:
                    connection.execute(
                        "ALTER TABLE projects ADD COLUMN priorities "
                        "TEXT NOT NULL DEFAULT ''"
                    )
                if "exclusions" not in columns:
                    connection.execute(
                        "ALTER TABLE projects ADD COLUMN exclusions "
                        "TEXT NOT NULL DEFAULT ''"
                    )
            connection.commit()

    def create_project(
        self: Any,
        user_id: int,
        name: str,
        niche: str,
        region: str,
        *,
        category_code: str = "custom",
        category_name: str = "Своя ниша",
        offer: str = "",
        target_audience: str = "",
        advantage: str = "",
        priorities: str = "",
        exclusions: str = "",
    ) -> int:
        statement = self._sql(
            """
            INSERT INTO projects (
                user_id, name, category_code, category_name, niche, offer,
                target_audience, region, advantage, priorities, exclusions, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
            RETURNING id
            """
        )
        with self._connect() as connection:
            row = connection.execute(
                statement,
                (
                    user_id,
                    name,
                    category_code,
                    category_name,
                    niche,
                    offer,
                    target_audience,
                    region,
                    advantage,
                    priorities,
                    exclusions,
                ),
            ).fetchone()
            connection.commit()
        return int(row["id"])

    def list_projects(
        self: Any, user_id: int, limit: int = 20
    ) -> list[dict[str, Any]]:
        statement = self._sql(
            """
            SELECT id, name, category_code, category_name, niche, offer,
                   target_audience, region, advantage, priorities, exclusions,
                   status, created_at
            FROM projects
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """
        )
        with self._connect() as connection:
            rows = connection.execute(statement, (user_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def get_project(
        self: Any, user_id: int, project_id: int
    ) -> dict[str, Any] | None:
        statement = self._sql(
            """
            SELECT id, name, category_code, category_name, niche, offer,
                   target_audience, region, advantage, priorities, exclusions,
                   status, created_at
            FROM projects
            WHERE user_id = ? AND id = ?
            """
        )
        with self._connect() as connection:
            row = connection.execute(statement, (user_id, project_id)).fetchone()
        return dict(row) if row else None

    async def select_project_category(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
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
        context.user_data["project_questionnaire"] = _profile(code)
        await query.message.reply_text(
            f"{label}\n\n"
            + _question(1, _profile(code)["name"]),
            reply_markup=ReplyKeyboardRemove(),
        )
        return PROJECT_NAME

    async def receive_project_name(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        name = (update.effective_message.text or "").strip()
        if not 3 <= len(name) <= 120:
            await update.effective_message.reply_text(
                "Название должно содержать от 3 до 120 символов."
            )
            return PROJECT_NAME
        context.user_data["project_name"] = name
        profile = context.user_data["project_questionnaire"]
        await update.effective_message.reply_text(_question(2, profile["niche"]))
        return PROJECT_NICHE

    async def receive_project_niche(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        niche = (update.effective_message.text or "").strip()
        if len(niche) < 2:
            await update.effective_message.reply_text("Укажите нишу точнее.")
            return PROJECT_NICHE
        context.user_data["project_niche"] = niche
        profile = context.user_data["project_questionnaire"]
        await update.effective_message.reply_text(_question(3, profile["offer"]))
        return PROJECT_OFFER

    async def receive_project_offer(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        offer = (update.effective_message.text or "").strip()
        if len(offer) < 3:
            await update.effective_message.reply_text(
                "Опишите услугу или продукт минимум тремя символами."
            )
            return PROJECT_OFFER
        context.user_data["project_offer"] = offer
        profile = context.user_data["project_questionnaire"]
        await update.effective_message.reply_text(_question(4, profile["audience"]))
        return PROJECT_AUDIENCE

    async def receive_project_audience(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        audience = (update.effective_message.text or "").strip()
        if len(audience) < 3:
            await update.effective_message.reply_text(
                "Опишите целевого клиента минимум тремя символами."
            )
            return PROJECT_AUDIENCE
        context.user_data["project_audience"] = audience
        profile = context.user_data["project_questionnaire"]
        await update.effective_message.reply_text(_question(5, profile["region"]))
        return PROJECT_REGION

    async def receive_project_region(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        region = (update.effective_message.text or "").strip()
        if len(region) < 2:
            await update.effective_message.reply_text("Укажите регион точнее.")
            return PROJECT_REGION
        context.user_data["project_region"] = region
        profile = context.user_data["project_questionnaire"]
        await update.effective_message.reply_text(_question(6, profile["advantage"]))
        return PROJECT_ADVANTAGE

    async def receive_project_advantage(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        advantage = (update.effective_message.text or "").strip()
        if len(advantage) < 3:
            await update.effective_message.reply_text(
                "Уточните пользу или решаемую проблему минимум тремя символами."
            )
            return PROJECT_ADVANTAGE
        context.user_data["project_advantage"] = advantage
        profile = context.user_data["project_questionnaire"]
        await update.effective_message.reply_text(_question(7, profile["priorities"]))
        return PROJECT_PRIORITIES

    async def receive_project_priorities(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        priorities = (update.effective_message.text or "").strip()
        if len(priorities) < 3:
            await update.effective_message.reply_text(
                "Опишите приоритетных клиентов или заказы минимум тремя символами."
            )
            return PROJECT_PRIORITIES
        context.user_data["project_priorities"] = priorities
        profile = context.user_data["project_questionnaire"]
        await update.effective_message.reply_text(_question(8, profile["exclusions"]))
        return PROJECT_EXCLUSIONS

    async def receive_project_exclusions(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        exclusions = (update.effective_message.text or "").strip()
        if len(exclusions) < 2:
            await update.effective_message.reply_text(
                "Укажите исключения или отправьте «нет», если исключений нет."
            )
            return PROJECT_EXCLUSIONS
        if exclusions.lower() in {"нет", "-", "никаких", "без исключений"}:
            exclusions = ""
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
            advantage=str(context.user_data["project_advantage"]),
            priorities=str(context.user_data["project_priorities"]),
            exclusions=exclusions,
        )
        exclusions_text = exclusions or "нет"
        await update.effective_message.reply_text(
            "✅ Проект активирован\n\n"
            f"📁 {context.user_data['project_name']}\n\n"
            f"Направление: {context.user_data['project_category_name']}\n"
            f"Ниша: {context.user_data['project_niche']}\n"
            f"Приоритет: {context.user_data['project_priorities']}\n"
            f"Исключения: {exclusions_text}\n"
            "Статус: ✅ Активен\n"
            "Анкета: 8 / 8\n\n"
            "Проект сохранён. Ответы будут использоваться при формировании "
            "поискового запроса и проверке релевантности лидов.\n"
            "Нажмите «🔎 Найти клиентов» и выберите этот проект.",
            reply_markup=MENU,
        )
        context.user_data.clear()
        return ConversationHandler.END

    async def list_projects(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
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
                    "priorities",
                    "exclusions",
                )
            )
            # «Нет исключений» — тоже полноценный ответ восьмого шага.
            if not str(project.get("exclusions") or "").strip():
                answered = min(8, answered + 1)
            text.append(
                f"📁 {project['name']}\n"
                f"Направление: {project['category_name']}\n"
                f"Ниша: {project['niche']}\n"
                f"Приоритет: {project.get('priorities') or 'не указан'}\n"
                f"Статус: ✅ Активен\n"
                f"Анкета: {answered} / 8\n"
                f"ID проекта: {project['id']}"
            )
        await update.effective_message.reply_text("\n\n".join(text), reply_markup=MENU)

    old_build_application = bot_class.build_application

    @wraps(old_build_application)
    def build_application(self: Any):
        application = old_build_application(self)
        for handlers in application.handlers.values():
            for handler in handlers:
                if not isinstance(handler, ConversationHandler):
                    continue
                if PROJECT_ADVANTAGE not in handler.states:
                    continue
                handler.states[PROJECT_PRIORITIES] = [
                    MessageHandler(USER_INPUT_FILTER, self.receive_project_priorities)
                ]
                handler.states[PROJECT_EXCLUSIONS] = [
                    MessageHandler(USER_INPUT_FILTER, self.receive_project_exclusions)
                ]
                return application
        raise RuntimeError("Не найден основной ConversationHandler LeadPilot")

    database_class.init_schema = init_schema
    database_class.create_project = create_project
    database_class.list_projects = list_projects
    database_class.get_project = get_project

    bot_class.select_project_category = select_project_category
    bot_class.receive_project_name = receive_project_name
    bot_class.receive_project_niche = receive_project_niche
    bot_class.receive_project_offer = receive_project_offer
    bot_class.receive_project_audience = receive_project_audience
    bot_class.receive_project_region = receive_project_region
    bot_class.receive_project_advantage = receive_project_advantage
    bot_class.receive_project_priorities = receive_project_priorities
    bot_class.receive_project_exclusions = receive_project_exclusions
    bot_class.list_projects = list_projects
    bot_class.build_application = build_application
    bot_class._project_questionnaires_installed = True
