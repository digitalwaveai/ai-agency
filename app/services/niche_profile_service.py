from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    NicheCategory,
    NicheProfile,
    ProjectAnswer,
    QuestionnaireQuestion,
    QuestionnaireTemplate,
    User,
    UserProject,
)


class NicheProfileError(ValueError):
    pass


@dataclass(frozen=True)
class QuestionDefinition:
    key: str
    label: str
    question_type: str
    options: list[str]
    required: bool = True
    help_text: str = ""


@dataclass(frozen=True)
class ProfileDefinition:
    category_code: str
    code: str
    name: str
    description: str
    seller_label: str
    target_label: str
    config: dict[str, Any]
    questions: list[QuestionDefinition]
    is_custom: bool = False


CATEGORIES = [
    {"code": "beauty", "name": "Beauty и здоровье", "emoji": "💅", "sort_order": 10},
    {"code": "finance", "name": "Финансы и образование", "emoji": "📈", "sort_order": 20},
    {"code": "marketing", "name": "Маркетинг и продажи", "emoji": "📣", "sort_order": 30},
    {"code": "development", "name": "Разработка и IT", "emoji": "💻", "sort_order": 40},
    {"code": "content", "name": "Контент и видео", "emoji": "🎬", "sort_order": 50},
    {"code": "ecommerce", "name": "Маркетплейсы и e-commerce", "emoji": "🛒", "sort_order": 60},
    {"code": "custom", "name": "Своя ниша", "emoji": "🧩", "sort_order": 999},
]


def q(
    key: str,
    label: str,
    question_type: str = "text",
    options: list[str] | None = None,
    *,
    required: bool = True,
    help_text: str = "",
) -> QuestionDefinition:
    return QuestionDefinition(
        key=key,
        label=label,
        question_type=question_type,
        options=options or [],
        required=required,
        help_text=help_text,
    )


PROFILE_DEFINITIONS = [
    ProfileDefinition(
        category_code="beauty",
        code="beauty_expert",
        name="Beauty-специалисты",
        description="Поиск мастеров, салонов, клиник и учебных центров beauty-сферы.",
        seller_label="Специалист или агентство для beauty",
        target_label="Beauty-бизнес",
        config={
            "positive_keywords": [
                "косметолог", "салон красоты", "бровист", "маникюр",
                "ресницы", "клиника эстетической медицины", "массаж",
            ],
            "default_exclusions": [
                "крупные сети", "франшизы", "каталоги", "агрегаторы",
                "страницы отзывов", "вакансии",
            ],
            "pain_signals": [
                "запись через личные сообщения",
                "нет онлайн-записи",
                "нет автоматических напоминаний",
                "слабое присутствие в соцсетях",
                "нет сайта или сайт устарел",
            ],
            "offer_examples": [
                "Telegram-бот для записи",
                "сайт и онлайн-запись",
                "CRM и автоматические напоминания",
                "маркетинг и упаковка соцсетей",
            ],
        },
        questions=[
            q("specialization", "Ваша специализация?", "single_choice",
              ["Косметолог", "Мастер маникюра", "Бровист", "Лешмейкер", "Салон", "Клиника", "Другое"]),
            q("service", "Какую услугу вы продаёте beauty-клиентам?", "multiple_choice",
              ["Онлайн-запись", "Telegram-бот", "Реклама", "Сайт", "CRM", "Дизайн", "SMM"]),
            q("target_type", "Кого искать?", "multiple_choice",
              ["Частные мастера", "Салоны", "Клиники", "Учебные центры"]),
            q("target_services", "Какие услуги должны быть у потенциального клиента?", "text"),
            q("location", "В каком городе или регионе искать?", "text"),
            q("priority_pains", "Какие проблемы особенно важны?", "multiple_choice",
              ["Запись через сообщения", "Нет сайта", "Нет онлайн-записи", "Слабые соцсети", "Долгие ответы", "Нет напоминаний"]),
            q("required_contacts", "Какие контакты обязательны?", "multiple_choice",
              ["Телефон", "Telegram", "Email", "Сайт", "Соцсети"]),
            q("exclude_chains", "Исключать сети и франшизы?", "boolean", ["Да", "Нет"]),
            q("offer_budget", "Какой средний чек вашего предложения?", "number"),
            q("result_promise", "Какой измеримый результат вы можете дать клиенту?", "text"),
        ],
    ),
    ProfileDefinition(
        category_code="finance",
        code="trading_finance_content",
        name="Трейдинг и финансовый контент",
        description="Поиск клиентов для образовательных, аналитических и программных продуктов без обещаний гарантированной доходности.",
        seller_label="Автор финансового продукта",
        target_label="Финансовая аудитория или проект",
        config={
            "positive_keywords": [
                "обучение трейдингу", "финансовый канал", "инвестиционное сообщество",
                "аналитика рынка", "сервис для трейдеров", "риск-менеджмент",
            ],
            "default_exclusions": [
                "гарантированная прибыль", "безрисковый доход", "100% сигнал",
                "казино", "ставки", "финансовая пирамида",
            ],
            "pain_signals": [
                "нет понятной образовательной программы",
                "слабая упаковка канала",
                "ручная аналитика",
                "нет автоматизации сообщества",
                "нет системы риск-менеджмента",
            ],
            "compliance_rules": [
                "не обещать доходность",
                "не выдавать прогноз за гарантию",
                "не таргетировать несовершеннолетних",
            ],
        },
        questions=[
            q("offer_type", "Что именно вы предлагаете?", "single_choice",
              ["Обучение", "Аналитика", "Сообщество", "Контент", "Программный сервис"]),
            q("market", "С каким рынком работаете?", "multiple_choice",
              ["Акции", "Криптовалюты", "Forex", "Фьючерсы", "Общий финансовый рынок"]),
            q("target_audience", "Кого ищете?", "multiple_choice",
              ["Начинающие трейдеры", "Опытные трейдеры", "Авторы каналов", "Финансовые школы", "Инвестиционные сообщества"]),
            q("product_format", "Какой формат продукта?", "multiple_choice",
              ["Курс", "Консультация", "Подписка", "Платформа", "Контент"]),
            q("priority_pains", "Какие проблемы клиента решаете?", "multiple_choice",
              ["Нет торговой системы", "Нет риск-менеджмента", "Много ручной аналитики", "Слабое оформление канала", "Нет автоматизации сообщества"]),
            q("credentials", "Есть ли подтверждённая квалификация и публичные материалы?", "text"),
            q("forbidden_claims", "Какие формулировки нельзя использовать?", "multiple_choice",
              ["Гарантированная прибыль", "Безрисковый доход", "Точный сигнал", "Быстрый заработок"]),
            q("regions", "В каких странах и регионах разрешено работать?", "text"),
            q("excluded_audience", "Какую аудиторию исключать?", "text"),
            q("ethical_offer", "Какой честный и проверяемый оффер использовать?", "text"),
        ],
    ),
    ProfileDefinition(
        category_code="marketing",
        code="marketing_strategy",
        name="Маркетологи",
        description="Поиск компаний с проблемами в позиционировании, аналитике, воронках и повторных продажах.",
        seller_label="Маркетолог",
        target_label="Компания или эксперт",
        config={
            "positive_keywords": ["бизнес", "компания", "онлайн-школа", "e-commerce", "SaaS", "локальный бизнес"],
            "pain_signals": ["нет воронки", "нет аналитики", "слабое позиционирование", "слабая посадочная страница", "нет повторных продаж"],
            "offer_examples": ["маркетинговый аудит", "стратегия", "воронка", "аналитика", "сопровождение"],
        },
        questions=[
            q("specialization", "Ваша специализация?", "multiple_choice",
              ["Стратегия", "Аналитика", "Воронки", "Комплексный маркетинг", "Исследования"]),
            q("target_business", "Какие компании ищете?", "multiple_choice",
              ["Локальный бизнес", "E-commerce", "Эксперты", "B2B", "SaaS", "Онлайн-школы"]),
            q("channels", "Какие каналы вы улучшаете?", "multiple_choice",
              ["Сайт", "Реклама", "Email", "CRM", "Контент", "Продажи"]),
            q("business_size", "Какой минимальный размер бизнеса вам подходит?", "text"),
            q("priority_pains", "Какие проблемы искать?", "multiple_choice",
              ["Нет воронки", "Нет аналитики", "Непонятное позиционирование", "Слабые лендинги", "Нет повторных продаж"]),
            q("metrics", "Какие показатели вы можете улучшить?", "text"),
            q("excluded_industries", "С какими отраслями вы не работаете?", "text", required=False),
            q("engagement_type", "Что вы продаёте?", "single_choice",
              ["Аудит", "Проект", "Постоянное сопровождение"]),
            q("client_budget", "Какой бюджет клиента подходит?", "number"),
            q("required_evidence", "Какое доказательство проблемы обязательно?", "text"),
        ],
    ),
    ProfileDefinition(
        category_code="marketing",
        code="smm",
        name="SMM-специалисты",
        description="Поиск экспертов и компаний со слабым или нерегулярным ведением социальных сетей.",
        seller_label="SMM-специалист",
        target_label="Аккаунт или бренд",
        config={
            "positive_keywords": ["Telegram", "VK", "Instagram", "TikTok", "YouTube", "социальные сети"],
            "pain_signals": ["нерегулярные публикации", "слабое оформление", "нет коротких видео", "нет CTA", "низкая вовлечённость"],
            "offer_examples": ["ведение", "контент-стратегия", "упаковка", "Reels и Shorts", "контент-план"],
        },
        questions=[
            q("platforms", "С какими платформами вы работаете?", "multiple_choice",
              ["Telegram", "VK", "Instagram", "TikTok", "YouTube"]),
            q("content_types", "Какой контент создаёте?", "multiple_choice",
              ["Посты", "Reels", "Shorts", "Stories", "Дизайн", "Стратегия"]),
            q("target_type", "Кого ищете?", "multiple_choice",
              ["Эксперты", "Магазины", "Локальный бизнес", "Онлайн-школы", "B2B"]),
            q("priority_pains", "Какие признаки слабого ведения искать?", "multiple_choice",
              ["Нерегулярные публикации", "Слабое оформление", "Нет коротких видео", "Нет призывов к действию", "Низкая вовлечённость", "Нет ответов"]),
            q("existing_account", "Обязателен ли уже существующий аккаунт?", "boolean", ["Да", "Нет"]),
            q("minimum_audience", "Какой минимальный размер аудитории?", "number", required=False),
            q("source_materials", "Нужны ли видеоисходники у клиента?", "single_choice",
              ["Обязательно", "Желательно", "Не требуется"]),
            q("service_format", "Что предлагаете?", "single_choice",
              ["Полное ведение", "Разовая упаковка", "Контент-план", "Производство контента"]),
            q("existing_content", "Какой контент клиент уже должен публиковать?", "text", required=False),
            q("exclusions", "Какие аккаунты исключать?", "text"),
        ],
    ),
    ProfileDefinition(
        category_code="development",
        code="software_development",
        name="Программисты и разработчики",
        description="Поиск компаний, которым нужны сайты, приложения, боты, интеграции или технические доработки.",
        seller_label="Разработчик",
        target_label="Компания с технической задачей",
        config={
            "positive_keywords": ["сайт", "приложение", "CRM", "интернет-магазин", "бот", "SaaS", "автоматизация"],
            "pain_signals": ["медленный сайт", "сломанные формы", "нет мобильной версии", "ручные заявки", "устаревший интерфейс", "нет интеграции"],
            "offer_examples": ["технический аудит", "MVP", "интеграция", "исправление ошибок", "разработка"],
        },
        questions=[
            q("product_type", "Что вы разрабатываете?", "multiple_choice",
              ["Сайты", "Приложения", "Боты", "SaaS", "Интеграции", "Интернет-магазины"]),
            q("tech_stack", "Ваш основной стек?", "multiple_choice",
              ["Python", "JavaScript", "PHP", "React", "Мобильная разработка", "No-code", "Другое"]),
            q("target_business", "Кого ищете?", "multiple_choice",
              ["Стартапы", "Интернет-магазины", "Локальный бизнес", "Агентства", "Онлайн-школы", "B2B"]),
            q("project_size", "Какой размер проекта подходит?", "text"),
            q("priority_pains", "Какие технические проблемы искать?", "multiple_choice",
              ["Медленный сайт", "Сломанные формы", "Нет мобильной версии", "Ручная обработка заявок", "Устаревший дизайн", "Нет CRM-интеграции"]),
            q("work_type", "Работаете с новым продуктом или доработкой?", "multiple_choice",
              ["Новый продукт", "Доработка", "Оба варианта"]),
            q("source_code", "Нужен ли доступ к существующему коду?", "single_choice",
              ["Да", "Нет", "Зависит от задачи"]),
            q("minimum_budget", "Какой минимальный бюджет проекта?", "number"),
            q("excluded_tech", "Какие технологии или типы проектов исключить?", "text", required=False),
            q("first_step", "Что предлагаете первым шагом?", "single_choice",
              ["Аудит", "Прототип", "Исправление", "Разработка MVP", "Консультация"]),
        ],
    ),
    ProfileDefinition(
        category_code="content",
        code="video_editing",
        name="Видеомонтаж",
        description="Поиск блогеров, экспертов, онлайн-школ и компаний с потребностью в регулярном видеоконтенте.",
        seller_label="Видеомонтажёр",
        target_label="Автор или компания с видеоконтентом",
        config={
            "positive_keywords": ["YouTube", "Reels", "Shorts", "подкаст", "вебинар", "блогер", "эксперт"],
            "pain_signals": ["длинные видео без нарезок", "редкие публикации", "нет субтитров", "слабый хук", "нет динамики"],
            "offer_examples": ["тестовый ролик", "пакет Shorts", "монтаж YouTube", "нарезка подкаста"],
        },
        questions=[
            q("video_types", "Что вы монтируете?", "multiple_choice",
              ["Shorts", "Reels", "YouTube", "Подкасты", "Рекламные ролики", "Вебинары"]),
            q("target_type", "Кого ищете?", "multiple_choice",
              ["Блогеры", "Эксперты", "Онлайн-школы", "Компании", "Подкасты"]),
            q("platforms", "На каких платформах должен быть клиент?", "multiple_choice",
              ["YouTube", "Telegram", "VK", "Instagram", "TikTok"]),
            q("priority_pains", "Какие признаки потребности искать?", "multiple_choice",
              ["Длинные видео без нарезок", "Редкие публикации", "Нет субтитров", "Слабые первые секунды", "Нет динамики", "Один формат контента"]),
            q("source_materials", "Нужны ли готовые исходники?", "single_choice",
              ["Обязательно", "Желательно", "Не обязательно"]),
            q("additional_services", "Делаете ли сценарии, превью и обложки?", "multiple_choice",
              ["Сценарии", "Превью", "Обложки", "Субтитры", "Не делаю"]),
            q("monthly_capacity", "Какой объём роликов в месяц готовы брать?", "number"),
            q("editing_style", "Какой стиль монтажа используете?", "text"),
            q("minimum_channel_size", "Какой минимальный размер канала?", "number", required=False),
            q("entry_offer", "Что предлагаете первым шагом?", "single_choice",
              ["Тестовый ролик", "Пакет роликов", "Аудит контента", "Месячное сопровождение"]),
        ],
    ),
    ProfileDefinition(
        category_code="ecommerce",
        code="marketplace_card_design",
        name="Дизайн карточек маркетплейсов",
        description="Поиск продавцов и брендов со слабыми карточками товаров и визуальной упаковкой.",
        seller_label="Дизайнер карточек",
        target_label="Продавец или бренд на маркетплейсе",
        config={
            "positive_keywords": ["Wildberries", "Ozon", "Яндекс Маркет", "Amazon", "карточка товара", "маркетплейс"],
            "pain_signals": ["нет инфографики", "плохие фотографии", "много текста", "нет единого стиля", "не показаны преимущества"],
            "offer_examples": ["редизайн одной карточки", "пакет карточек", "rich-контент", "анализ конкурентов"],
        },
        questions=[
            q("marketplaces", "Для каких площадок работаете?", "multiple_choice",
              ["Wildberries", "Ozon", "Яндекс Маркет", "Amazon", "Другие"]),
            q("product_categories", "С какими категориями товаров работаете?", "text"),
            q("target_sellers", "Кого ищете?", "multiple_choice",
              ["Частные продавцы", "Бренды", "Производители", "Агентства"]),
            q("priority_pains", "Какие признаки слабых карточек искать?", "multiple_choice",
              ["Нет инфографики", "Плохие фотографии", "Слишком много текста", "Нет единого стиля", "Не показаны преимущества", "Слабая первая фотография"]),
            q("retouching", "Делаете ли предметную ретушь?", "boolean", ["Да", "Нет"]),
            q("rich_content", "Создаёте ли rich-контент?", "boolean", ["Да", "Нет"]),
            q("competitor_analysis", "Проводите ли анализ конкурентов?", "boolean", ["Да", "Нет"]),
            q("package_size", "Сколько карточек входит в ваш пакет?", "number"),
            q("client_sources", "Работаете ли с исходниками клиента?", "single_choice",
              ["Да", "Нет", "Могу организовать съёмку"]),
            q("entry_offer", "Что предлагаете первым шагом?", "single_choice",
              ["Редизайн одной карточки", "Пакет карточек", "Аудит магазина", "Полная упаковка бренда"]),
        ],
    ),
    ProfileDefinition(
        category_code="custom",
        code="custom_niche",
        name="Своя ниша",
        description="Универсальная анкета для любого сервисного или B2B-направления.",
        seller_label="Специалист",
        target_label="Потенциальный клиент",
        is_custom=True,
        config={
            "positive_keywords": [],
            "default_exclusions": ["каталоги", "агрегаторы", "вакансии", "мошеннические проекты"],
            "pain_signals": [],
            "offer_examples": [],
        },
        questions=[
            q("niche_name", "Как называется ваша ниша или специализация?", "text"),
            q("service", "Какую услугу или продукт вы продаёте?", "text"),
            q("target_customer", "Кого вы считаете идеальным клиентом?", "text"),
            q("business_model", "Вы работаете с B2B, B2C или обоими?", "multiple_choice",
              ["B2B", "B2C", "Оба"]),
            q("location", "В каком регионе искать клиентов?", "text"),
            q("priority_pains", "Какие проблемы клиента вы решаете?", "text"),
            q("required_signals", "Какие признаки показывают, что клиенту нужна ваша услуга?", "text"),
            q("required_contacts", "Какие контакты обязательны?", "multiple_choice",
              ["Телефон", "Telegram", "Email", "Сайт", "Соцсети"]),
            q("exclusions", "Каких клиентов и источники исключать?", "text"),
            q("entry_offer", "Что вы предлагаете клиенту первым шагом?", "text"),
        ],
    ),
]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def seed_niche_profiles(db: Session) -> dict[str, int]:
    category_by_code: dict[str, NicheCategory] = {}

    for item in CATEGORIES:
        category = db.scalar(
            select(NicheCategory).where(NicheCategory.code == item["code"])
        )
        if category is None:
            category = NicheCategory(**item)
            db.add(category)
            db.flush()
        else:
            category.name = item["name"]
            category.emoji = item["emoji"]
            category.sort_order = item["sort_order"]
            category.is_active = True
        category_by_code[item["code"]] = category

    profile_count = 0
    question_count = 0

    for definition in PROFILE_DEFINITIONS:
        profile = db.scalar(
            select(NicheProfile).where(NicheProfile.code == definition.code)
        )
        category = category_by_code[definition.category_code]
        if profile is None:
            profile = NicheProfile(
                category_id=category.id,
                code=definition.code,
                name=definition.name,
                description=definition.description,
                seller_label=definition.seller_label,
                target_label=definition.target_label,
                config_json=_json_dumps(definition.config),
                is_custom=definition.is_custom,
                is_active=True,
            )
            db.add(profile)
            db.flush()
        else:
            profile.category_id = category.id
            profile.name = definition.name
            profile.description = definition.description
            profile.seller_label = definition.seller_label
            profile.target_label = definition.target_label
            profile.config_json = _json_dumps(definition.config)
            profile.is_custom = definition.is_custom
            profile.is_active = True

        template = db.scalar(
            select(QuestionnaireTemplate).where(
                QuestionnaireTemplate.niche_profile_id == profile.id,
                QuestionnaireTemplate.version == 1,
            )
        )
        if template is None:
            template = QuestionnaireTemplate(
                niche_profile_id=profile.id,
                version=1,
                name=f"Анкета: {definition.name}",
                intro="Ответьте на вопросы, чтобы система настроила поиск клиентов.",
                is_active=True,
            )
            db.add(template)
            db.flush()
        else:
            template.name = f"Анкета: {definition.name}"
            template.is_active = True

        existing_questions = {
            row.question_key: row
            for row in db.scalars(
                select(QuestionnaireQuestion).where(
                    QuestionnaireQuestion.template_id == template.id
                )
            ).all()
        }

        desired_keys = set()
        for index, question in enumerate(definition.questions, start=1):
            desired_keys.add(question.key)
            row = existing_questions.get(question.key)
            if row is None:
                row = QuestionnaireQuestion(
                    template_id=template.id,
                    question_key=question.key,
                    label=question.label,
                    help_text=question.help_text,
                    question_type=question.question_type,
                    options_json=_json_dumps(question.options),
                    validation_json="{}",
                    show_if_json="{}",
                    is_required=question.required,
                    sort_order=index,
                )
                db.add(row)
            else:
                row.label = question.label
                row.help_text = question.help_text
                row.question_type = question.question_type
                row.options_json = _json_dumps(question.options)
                row.is_required = question.required
                row.sort_order = index
            question_count += 1

        for key, row in existing_questions.items():
            if key not in desired_keys:
                db.delete(row)

        profile_count += 1

    db.commit()
    return {
        "categories": len(CATEGORIES),
        "profiles": profile_count,
        "questions": question_count,
    }


def list_niche_profiles(db: Session, *, include_custom: bool = True) -> list[NicheProfile]:
    query = (
        select(NicheProfile)
        .where(NicheProfile.is_active.is_(True))
        .order_by(NicheProfile.is_custom.asc(), NicheProfile.name.asc())
    )
    if not include_custom:
        query = query.where(NicheProfile.is_custom.is_(False))
    return list(db.scalars(query).all())


def get_niche_profile(db: Session, code: str) -> NicheProfile | None:
    return db.scalar(
        select(NicheProfile).where(
            NicheProfile.code == code.strip().lower(),
            NicheProfile.is_active.is_(True),
        )
    )


def get_questionnaire(db: Session, profile_code: str) -> list[dict[str, Any]]:
    profile = get_niche_profile(db, profile_code)
    if profile is None:
        raise NicheProfileError("Профиль ниши не найден")

    template = db.scalar(
        select(QuestionnaireTemplate)
        .where(
            QuestionnaireTemplate.niche_profile_id == profile.id,
            QuestionnaireTemplate.is_active.is_(True),
        )
        .order_by(QuestionnaireTemplate.version.desc())
    )
    if template is None:
        raise NicheProfileError("Анкета для ниши не найдена")

    rows = db.scalars(
        select(QuestionnaireQuestion)
        .where(QuestionnaireQuestion.template_id == template.id)
        .order_by(QuestionnaireQuestion.sort_order.asc())
    ).all()

    return [
        {
            "key": row.question_key,
            "label": row.label,
            "help_text": row.help_text,
            "type": row.question_type,
            "options": _json_loads(row.options_json, []),
            "required": row.is_required,
            "sort_order": row.sort_order,
        }
        for row in rows
    ]


def create_user_project(
    db: Session,
    *,
    user_id: int,
    name: str,
    profile_code: str,
    custom_niche: str | None = None,
    now: datetime | None = None,
) -> UserProject:
    now = now or datetime.utcnow()
    if db.get(User, user_id) is None:
        raise NicheProfileError("Пользователь не найден")

    profile = get_niche_profile(db, profile_code)
    if profile is None:
        raise NicheProfileError("Профиль ниши не найден")
    if profile.is_custom and not (custom_niche or "").strip():
        raise NicheProfileError("Для своей ниши укажите название направления")

    project = UserProject(
        user_id=user_id,
        niche_profile_id=profile.id,
        name=name.strip(),
        custom_niche=(custom_niche or "").strip() or None,
        status="draft",
        summary_json="{}",
        created_at=now,
        updated_at=now,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def save_project_answer(
    db: Session,
    *,
    project_id: int,
    question_key: str,
    answer: Any,
    now: datetime | None = None,
) -> ProjectAnswer:
    now = now or datetime.utcnow()
    project = db.get(UserProject, project_id)
    if project is None:
        raise NicheProfileError("Проект не найден")

    key = question_key.strip()
    row = db.scalar(
        select(ProjectAnswer).where(
            ProjectAnswer.project_id == project_id,
            ProjectAnswer.question_key == key,
        )
    )
    if row is None:
        row = ProjectAnswer(
            project_id=project_id,
            question_key=key,
            answer_json=_json_dumps(answer),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.answer_json = _json_dumps(answer)
        row.updated_at = now

    db.commit()
    db.refresh(row)
    return row


def get_project_answers(db: Session, project_id: int) -> dict[str, Any]:
    rows = db.scalars(
        select(ProjectAnswer)
        .where(ProjectAnswer.project_id == project_id)
        .order_by(ProjectAnswer.id.asc())
    ).all()
    return {
        row.question_key: _json_loads(row.answer_json, None)
        for row in rows
    }


def complete_project(db: Session, project_id: int) -> UserProject:
    project = db.get(UserProject, project_id)
    if project is None:
        raise NicheProfileError("Проект не найден")

    profile = db.get(NicheProfile, project.niche_profile_id)
    if profile is None:
        raise NicheProfileError("Профиль проекта не найден")

    questionnaire = get_questionnaire(db, profile.code)
    answers = get_project_answers(db, project.id)
    missing = [
        item["key"]
        for item in questionnaire
        if item["required"] and item["key"] not in answers
    ]
    if missing:
        raise NicheProfileError(
            "Не заполнены обязательные вопросы: " + ", ".join(missing)
        )

    project.status = "active"
    project.summary_json = _json_dumps(
        {
            "profile_code": profile.code,
            "custom_niche": project.custom_niche,
            "answers": answers,
        }
    )
    project.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(project)
    return project
