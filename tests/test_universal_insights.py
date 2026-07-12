from app.schemas import LeadCreate, SearchRequest
from app.services.universal_insight_service import (
    analyze_universal_lead,
    generate_universal_outreach,
    score_universal_lead,
)


def lead(**kwargs):
    data = {
        "name": "Тестовый проект",
        "niche": "услуги",
        "city": "Москва",
        "description": "",
        "source_url": "https://example.com/project",
        "score": 0,
    }
    data.update(kwargs)
    return LeadCreate(**data)


def request(**kwargs):
    data = {
        "niche": "эксперты",
        "city": "Москва",
        "target_type": "эксперты",
        "services": ["услуга"],
        "target_pain": "",
        "strict_match": False,
    }
    data.update(kwargs)
    return SearchRequest(**data)


def context(profile_code, **kwargs):
    data = {
        "profile_code": profile_code,
        "profile_name": profile_code,
        "custom_niche": "",
        "pain_signals": [],
        "offer_examples": [],
    }
    data.update(kwargs)
    return data


def test_smm_detects_missing_short_video():
    item = lead(
        name="Школа английского",
        description="Онлайн-школа Москва. У нас только длинные видео, Shorts пока нет.",
    )
    req = request(
        niche="Онлайн-школы",
        target_type="Онлайн-школы",
        services=["Reels", "Shorts"],
        target_pain="Нет коротких видео",
    )
    result = analyze_universal_lead(item, req, context("smm"))
    assert "Нет коротких видео" in result.pain_points
    assert "Подтверждение:" in result.pain_points
    assert "Reels/Shorts" in result.suggested_offer


def test_video_detects_missing_subtitles():
    item = lead(
        name="Подкаст предпринимателя",
        description="Новый выпуск подкаста. Видео без субтитров.",
    )
    result = analyze_universal_lead(
        item,
        request(niche="Подкасты", target_type="Подкасты"),
        context("video_editing"),
    )
    assert "отсутствуют субтитры" in result.pain_points.lower()
    assert "субтитрами" in result.suggested_offer


def test_development_detects_manual_requests():
    item = lead(
        name="Сервис доставки",
        description="Чтобы заказать, напишите менеджеру в WhatsApp.",
    )
    result = analyze_universal_lead(
        item,
        request(niche="Сервис доставки", target_type="Сервис доставки"),
        context("software_development"),
    )
    assert "вручную" in result.pain_points.lower()
    assert "CRM" in result.suggested_offer


def test_marketplace_detects_missing_infographics():
    item = lead(
        name="Магазин товаров",
        description="Карточки Wildberries без инфографики, только фото товара.",
    )
    result = analyze_universal_lead(
        item,
        request(niche="Магазины Wildberries", target_type="Магазины"),
        context("marketplace_card_design"),
    )
    assert "нет инфографики" in result.pain_points.lower()
    assert "редизайн" in result.suggested_offer.lower()


def test_custom_profile_uses_target_pain_evidence():
    item = lead(
        name="Кофейня Север",
        description="Кофейня Москва. Обслуживание кофемашин выполняется вручную по звонку.",
    )
    req = request(
        niche="Кофейни",
        target_type="Кофейни",
        services=["Обслуживание кофемашин"],
        target_pain="обслуживание выполняется вручную",
    )
    result = analyze_universal_lead(
        item,
        req,
        context(
            "custom_niche",
            offer_examples=["Абонентское обслуживание кофемашин"],
        ),
    )
    assert "Подтверждение:" in result.pain_points
    assert "вручную" in result.pain_points.lower()


def test_no_evidence_does_not_invent_confirmed_pain():
    item = lead(
        name="Компания Альфа",
        description="Компания оказывает услуги в Москве.",
    )
    result = analyze_universal_lead(
        item,
        request(target_pain="нет автоматизации заявок"),
        context(
            "custom_niche",
            pain_signals=["нет автоматизации заявок"],
            offer_examples=["автоматизация заявок"],
        ),
    )
    assert result.pain_points == "Боль не подтверждена явным текстом"
    assert "Подтверждение:" not in result.pain_points


def test_confirmed_pain_scores_higher_than_unconfirmed():
    req = request(
        niche="Онлайн-школы",
        target_type="Онлайн-школы",
        services=["Shorts"],
    )
    ctx = context("smm", profile_name="SMM")
    confirmed = lead(
        name="Онлайн-школа Альфа",
        description="Онлайн-школа Москва. У нас нет коротких видео.",
        telegram_url="https://t.me/school_alpha",
        pain_points="Нет коротких видео\nПодтверждение: «У нас нет коротких видео»",
        suggested_offer="Пакет Shorts",
    )
    unconfirmed = lead(
        name="Онлайн-школа Бета",
        description="Онлайн-школа Москва.",
        telegram_url="https://t.me/school_beta",
        pain_points="Боль не подтверждена явным текстом",
        suggested_offer="Пакет Shorts",
    )
    confirmed_score, _ = score_universal_lead(confirmed, req, ctx)
    unconfirmed_score, _ = score_universal_lead(unconfirmed, req, ctx)
    assert confirmed_score > unconfirmed_score
    assert unconfirmed_score <= 70


def test_directory_is_rejected():
    item = lead(
        name="Каталог компаний",
        description="Рейтинг и каталог компаний Москвы",
        source_url="https://zoon.ru/msk/",
    )
    score, reason = score_universal_lead(
        item,
        request(),
        context("custom_niche"),
    )
    assert score == 0
    assert "каталог" in reason.lower()


def test_outreach_is_universal_not_beauty_specific():
    item = lead(
        name="Онлайн-школа Альфа | официальный сайт",
        pain_points="Нет коротких видео\nПодтверждение: «Публикуем только длинные уроки»",
        suggested_offer="пакет Shorts из существующих уроков",
    )
    messages = generate_universal_outreach(
        item,
        context("video_editing", profile_name="Видеомонтаж"),
    )
    assert {"soft", "business", "short"} == set(messages)
    assert "бьюти" not in messages["soft"].lower()
    assert "Shorts" in messages["short"]
    assert "Онлайн-школа Альфа" in messages["soft"]


def test_outreach_without_evidence_remains_honest():
    item = lead(
        name="Компания",
        pain_points="Боль не подтверждена явным текстом",
        suggested_offer="аудит",
    )
    messages = generate_universal_outreach(
        item,
        context("marketing_strategy", profile_name="Маркетинг"),
    )
    assert "Боль не подтверждена" in messages["business"]
