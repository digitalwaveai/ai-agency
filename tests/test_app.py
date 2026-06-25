import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("DEMO_MODE", "true")
os.environ.setdefault("SEARCH_PROVIDER", "demo")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Lead
from app.schemas import LeadCreate, SearchRequest
from app.services.deduplication import find_duplicate
from app.services.export import leads_to_csv
from app.services.scoring import score_lead
from app.services.search_service import generate_queries
from app.services.search_quality import assess_candidate_text

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def reset_test_db() -> None:
    # Importing Lead above registers the model on Base.metadata before create_all().
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def test_database():
    reset_test_db()
    yield
    Base.metadata.drop_all(bind=engine)


def sample_lead(**kw):
    data = dict(
        name="Косметолог Анна",
        niche="косметолог",
        city="Москва",
        country="Россия",
        website_url="https://anna.example.com",
        instagram_url="https://instagram.com/anna",
        email="anna@example.com",
        phone="+79990000000",
        whatsapp="+79990000000",
        description="Косметолог Москва, запись через WhatsApp, сайта нет, консультации",
        pain_points="ручная запись, нет сайта",
        suggested_offer="сайт",
        source_url="https://example.com/anna",
        source_type="demo",
        score=0,
    )
    data.update(kw)
    return LeadCreate(**data)


def test_generate_queries():
    qs = generate_queries(SearchRequest(niche="косметолог", city="Москва", services=["сайт"]))
    assert any("косметолог Москва" in q for q in qs)
    assert any("WhatsApp" in q for q in qs)


def test_scoring():
    score, reason = score_lead(sample_lead(), SearchRequest(niche="косметолог", city="Москва"))
    assert score >= 80
    assert "контакт" in reason


def test_save_and_filter_lead():
    payload = {"niche": "косметолог", "city": "Москва", "country": "Россия", "services": ["сайт"], "limit": 2, "min_score": 0, "contacts_only": False}
    r = client.post("/search", json=payload)
    assert r.status_code == 200
    assert len(r.json()) >= 1

    r = client.get("/leads", params={"city": "Москва", "min_score": 1})
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert all(item["city"] == "Москва" for item in r.json())


def test_deduplication_by_email():
    db = TestingSessionLocal()
    lead_data = sample_lead(email="dup@example.com", score=90).model_dump()
    lead = Lead(**lead_data)
    db.add(lead)
    db.commit()

    assert find_duplicate(db, sample_lead(email="dup@example.com")) is not None
    db.close()


def test_export_csv():
    db = TestingSessionLocal()
    db.add(Lead(**sample_lead(score=88).model_dump()))
    db.commit()

    csv_text = leads_to_csv(db.query(Lead).all())
    assert "name" in csv_text
    assert "source_url" in csv_text
    assert "Косметолог Анна" in csv_text
    db.close()


def test_outreach_generation():
    client.post("/search", json={"niche": "косметолог", "city": "Москва", "limit": 1})
    r = client.get("/leads")
    lead_id = r.json()[0]["id"]

    msg = client.post(f"/leads/{lead_id}/outreach")
    assert msg.status_code == 200
    assert {"soft", "business", "short"}.issubset(msg.json())



def test_queries_do_not_use_offered_services_as_search_need():
    request = SearchRequest(
        niche="косметолог",
        city="Москва",
        services=["дорогой корпоративный сайт"],
    )
    queries = generate_queries(request)
    assert all("дорогой корпоративный сайт" not in query for query in queries)


def test_quality_rejects_chain():
    decision = assess_candidate_text(
        title='Центр косметологии — сеть салонов "Зеркала красоты"',
        snippet="Федеральная сеть, 18 филиалов в Москве",
        url="https://zerkalastudio.ru/cosmetology",
        niche="косметолог",
        city="Москва",
        exclude="крупные сети, франшизы",
        require_city=True,
    )
    assert decision.accepted is False


def test_quality_rejects_directory():
    decision = assess_candidate_text(
        title="Лучшие косметологи Москвы — рейтинг и отзывы",
        snippet="Каталог специалистов, цены и отзывы клиентов",
        url="https://zoon.ru/msk/beauty/cosmetology/",
        niche="косметолог",
        city="Москва",
        require_city=True,
    )
    assert decision.accepted is False


def test_quality_accepts_private_specialist():
    decision = assess_candidate_text(
        title="Частный косметолог Анна в Москве",
        snippet="Принимаю в кабинете. Чистки, пилинги. Запись через WhatsApp.",
        url="https://vk.com/cosmetolog_anna_msk",
        niche="косметолог",
        city="Москва",
        require_city=True,
    )
    assert decision.accepted is True
    assert decision.score >= 70


def test_quality_rejects_generic_vk_wall_post():
    decision = assess_candidate_text(
        title="КОСМЕТОЛОГ МОСКВА",
        snippet="Услуги косметолога в Москве",
        url="https://vk.com/wall-219849546_2690",
        niche="косметолог",
        city="Москва",
        require_city=False,
    )
    assert decision.accepted is False


def test_quality_rejects_generic_telegram_message():
    decision = assess_candidate_text(
        title="КОСМЕТОЛОГ МОСКВА (Контурная пластика)",
        snippet="Косметолог Москва, контурная пластика",
        url="https://t.me/makeupstav26/363",
        niche="косметолог",
        city="Москва",
        require_city=False,
    )
    assert decision.accepted is False


def test_quality_rejects_generic_specialist_listing():
    decision = assess_candidate_text(
        title="Специалисты косметологи в Москве",
        snippet="Косметологи, цены и запись на прием",
        url="https://clinic-example.ru/doctors/cosmetolog/",
        niche="косметолог",
        city="Москва",
        require_city=False,
    )
    assert decision.accepted is False


def test_quality_rejects_large_medical_brand():
    decision = assess_candidate_text(
        title="Косметолог в Москве — запись на консультацию в MEDSI BEAUTY",
        snippet="Личный кабинет, мобильное приложение и сеть клиник",
        url="https://medsi.ru/services/kosmetologiya/",
        niche="косметолог",
        city="Москва",
        require_city=False,
    )
    assert decision.accepted is False


def test_quality_rejects_large_staff_count():
    decision = assess_candidate_text(
        title="СМ-Косметология в Москве",
        snippet="8 клиник и более 70 специалистов. Онлайн-запись и мобильное приложение.",
        url="https://sm-estetica.ru/",
        niche="косметолог",
        city="Москва",
        require_city=False,
    )
    assert decision.accepted is False


def test_quality_accepts_named_social_post():
    decision = assess_candidate_text(
        title="Анна — косметолог Москва",
        snippet="Для записи пишите в комментарии или WhatsApp +7 999 111-22-33",
        url="https://vk.com/wall-123456_789",
        niche="косметолог",
        city="Москва",
        require_city=False,
    )
    assert decision.accepted is True
    assert decision.score >= 70


def test_generic_instagram_profile_cannot_score_100():
    from app.services.search_service import SearchResult
    from app.services.search_quality import rank_search_results

    request = SearchRequest(
        niche="косметолог",
        city="Москва",
        limit=5,
        strict_match=False,
    )
    results = rank_search_results(
        [
            SearchResult(
                title="Косметолог | косметолог | Москва",
                url="https://www.instagram.com/8karinaaaa/",
                snippet="Услуги косметолога. Запись в директ.",
            )
        ],
        request,
        5,
    )
    assert len(results) == 1
    assert results[0].quality_score <= 60


def test_social_post_is_canonicalized_to_profile():
    from app.services.search_quality import canonical_social_profile_url

    assert canonical_social_profile_url(
        "https://vk.com/wall-219849546_2690"
    ) == "https://vk.com/club219849546"
    assert canonical_social_profile_url(
        "https://t.me/makeupstav26/363"
    ) == "https://t.me/makeupstav26"


def test_scoring_caps_profile_without_identity():
    lead = sample_lead(
        name="Профиль @8karinaaaa",
        website_url=None,
        instagram_url="https://www.instagram.com/8karinaaaa/",
        email=None,
        phone=None,
        whatsapp=None,
        description="Косметолог Москва. Услуги. Запись в директ.",
        pain_points="Выбранная боль не подтверждена",
        suggested_offer="онлайн-запись",
        source_url="https://www.instagram.com/8karinaaaa/",
    )
    request = SearchRequest(
        niche="косметолог",
        city="Москва",
        services=["онлайн-запись"],
        target_pain="запись через личные сообщения",
        strict_match=False,
    )
    score, _ = score_lead(lead, request)
    assert score <= 60


def test_scoring_named_private_specialist_can_be_high():
    lead = sample_lead(
        name="Анна",
        website_url=None,
        instagram_url=None,
        vk_url="https://vk.com/anna_cosmetolog",
        email=None,
        phone="+79991112233",
        whatsapp="+79991112233",
        description="Анна — частный косметолог в Москве. Для записи пишите в комментарии или WhatsApp.",
        pain_points="Ручная запись через сообщения\nПодтверждение: «Для записи пишите в комментарии или WhatsApp»",
        suggested_offer="онлайн-запись, Telegram-бот",
        source_url="https://vk.com/wall123456_789",
    )
    request = SearchRequest(
        niche="косметолог",
        city="Москва",
        services=["онлайн-запись", "Telegram-бот"],
        target_pain="запись через личные сообщения",
        strict_match=False,
    )
    score, _ = score_lead(lead, request)
    assert score >= 85


def test_quality_rejects_pinterest_publication():
    decision = assess_candidate_text(
        title="Коррекция носогубной складки | До и после",
        snippet="ОЛЬГА | КОСМЕТОЛОГ | МОСКВА",
        url="https://www.pinterest.com/pin/907967974884840793/",
        niche="косметолог",
        city="Москва",
    )
    assert decision.accepted is False


def test_quality_rejects_tgstat_mirror():
    decision = assess_candidate_text(
        title="COSMOTRADE",
        snippet="Канал о косметологии в Москве",
        url="https://tgstat.ru/en/channel/@cosmotrade_msk",
        niche="косметолог",
        city="Москва",
    )
    assert decision.accepted is False


def test_quality_rejects_livejournal_tag_page():
    decision = assess_candidate_text(
        title="Parfumerka, posts by tag: нишевая парфюмерия",
        snippet="Публикации по тегу косметология и парфюмерия",
        url="https://parfumerka.livejournal.com/tag/нишевая+парфюмерия",
        niche="косметолог",
        city="Москва",
    )
    assert decision.accepted is False


def test_generic_seo_landing_is_review_only():
    decision = assess_candidate_text(
        title="Косметология для мужчин в Москве",
        snippet="Контакты: info@kosmetolog-y-moskve.ru. Услуги косметолога.",
        url="https://kosmetolog-y-moskve.ru/",
        niche="косметолог",
        city="Москва",
    )
    assert decision.accepted is True
    assert decision.score <= 45


def test_generic_contact_page_is_review_only():
    decision = assess_candidate_text(
        title="Косметологический кабинет контакты",
        snippet="Москва, телефон +7 977 379-90-37, услуги косметолога",
        url="https://cosmetology-office.example.org/contacts",
        niche="косметолог",
        city="Москва",
    )
    assert decision.accepted is True
    assert decision.score <= 45


def test_target_pain_rejects_existing_online_booking():
    decision = assess_candidate_text(
        title="Косметолог Галина МАРС, Москва — онлайн-запись",
        snippet="Записаться онлайн на процедуру косметолога",
        url="https://my-kosmetolog.ru/booking",
        niche="косметолог",
        city="Москва",
        target_pain="запись через личные сообщения",
    )
    assert decision.accepted is False
    assert "онлайн-запись" in decision.reasons[0]


def test_named_lead_without_explicit_pain_cannot_score_100():
    lead = sample_lead(
        name="ТВОЙ КОСМЕТОЛОГ (@dr.beautysense)",
        website_url=None,
        instagram_url="https://www.instagram.com/dr.beautysense/",
        email=None,
        phone="+79774561757",
        whatsapp=None,
        description="Косметолог Москва. Услуги и консультации.",
        pain_points="Выбранная боль не подтверждена",
        suggested_offer="онлайн-запись, Telegram-бот",
        source_url="https://www.instagram.com/dr.beautysense/",
    )
    request = SearchRequest(
        niche="косметолог",
        city="Москва",
        services=["онлайн-запись", "Telegram-бот"],
        target_pain="запись через личные сообщения",
        strict_match=False,
    )
    score, _ = score_lead(lead, request)
    assert score < 100


def test_social_post_source_becomes_profile_url():
    from app.services.lead_enrichment import result_to_lead
    from app.services.search_service import SearchResult

    request = SearchRequest(niche="косметолог", city="Москва")
    lead = result_to_lead(
        SearchResult(
            title="Анна — косметолог Москва",
            url="https://vk.com/wall-123456_789",
            snippet="Для записи пишите в комментарии или WhatsApp +79991112233",
        ),
        request,
    )
    assert lead.source_url == "https://vk.com/club123456"
    assert lead.vk_url == "https://vk.com/club123456"


def test_negated_online_booking_is_not_a_contradiction():
    from app.services.search_quality import target_pain_contradiction_reason

    assert target_pain_contradiction_reason(
        "На проверенной странице не найдена явная онлайн-запись",
        "нет онлайн-записи",
    ) is None
    assert target_pain_contradiction_reason(
        "Онлайн-запись на странице не обнаружена",
        "запись через личные сообщения",
    ) is None


def test_quality_rejects_lashmaker_for_cosmetologist():
    decision = assess_candidate_text(
        title="НАРАЩИВАНИЕ РЕСНИЦ! Косметолог! Москва!",
        snippet="Мастер по ресницам, запись в сообщения",
        url="https://vk.com/public78610651",
        niche="косметолог",
        city="Москва",
    )
    assert decision.accepted is False
    assert "смежной beauty-нише" in decision.reasons[0]


def test_quality_rejects_makeup_studio_for_cosmetologist():
    decision = assess_candidate_text(
        title="Кристина | косметолог | Москва",
        snippet="Студия красоты",
        url="https://www.facebook.com/makeup.studio.kristina/",
        niche="косметолог",
        city="Москва",
    )
    assert decision.accepted is False


def test_quality_accepts_cosmetologist_with_core_procedures_and_brows():
    decision = assess_candidate_text(
        title="Анна — врач-косметолог Москва",
        snippet=(
            "Инъекционная косметология, пилинги, чистка лица и оформление бровей. "
            "Запись в WhatsApp +79991112233"
        ),
        url="https://anna-cosmetolog.ru/",
        niche="косметолог",
        city="Москва",
    )
    assert decision.accepted is True


def test_quality_rejects_facebook_as_unverifiable_source():
    decision = assess_candidate_text(
        title="Кристина — косметолог — Москва",
        snippet="Косметологические услуги",
        url="https://www.facebook.com/kristina.cosmetolog/",
        niche="косметолог",
        city="Москва",
    )
    assert decision.accepted is False
    assert "авторизац" in decision.reasons[0]


def test_quality_rejects_access_block_message():
    decision = assess_candidate_text(
        title="Косметолог Кристина Москва",
        snippet="Log in or sign up to view. See posts, photos and more on Facebook.",
        url="https://kristina.example.net/",
        niche="косметолог",
        city="Москва",
    )
    assert decision.accepted is False
    assert "недоступно" in decision.reasons[0]


def test_scoring_rejects_adjacent_lash_niche():
    lead = sample_lead(
        name="НАРАЩИВАНИЕ РЕСНИЦ! Косметолог! Москва!",
        website_url=None,
        vk_url="https://vk.com/public78610651",
        description="Мастер по ресницам. Запись в сообщения.",
        pain_points=(
            "Запись ведётся вручную через сообщения\n"
            "Подтверждение: «Для записи пишите в сообщения»"
        ),
        suggested_offer="онлайн-запись",
        source_url="https://vk.com/public78610651",
    )
    request = SearchRequest(
        niche="косметолог",
        city="Москва",
        services=["онлайн-запись"],
        target_pain="запись через личные сообщения",
        strict_match=False,
    )
    score, reason = score_lead(lead, request)
    assert score == 0
    assert "смежной beauty-нише" in reason


def test_search_queries_exclude_adjacent_beauty_niches_for_cosmetologist():
    from app.services.search_service import generate_queries

    request = SearchRequest(
        niche="косметолог",
        city="Москва",
        target_pain="запись через личные сообщения",
    )
    combined = " ".join(generate_queries(request)).lower()
    assert '-"наращивание ресниц"' in combined
    assert "-визажист" in combined
    assert "-маникюр" in combined
    assert "-facebook" in combined


def test_score_without_pain_evidence_is_capped_at_70():
    lead = sample_lead(
        name="ТВОЙ КОСМЕТОЛОГ (@dr.beautysense)",
        website_url=None,
        instagram_url="https://www.instagram.com/dr.beautysense/",
        email=None,
        phone="+79774561757",
        whatsapp=None,
        description="Частный косметолог Москва. Для записи пишите в директ.",
        pain_points="Запись ведётся вручную через сообщения",
        suggested_offer="онлайн-запись, Telegram-бот",
        source_url="https://www.instagram.com/dr.beautysense/",
    )
    request = SearchRequest(
        niche="косметолог",
        city="Москва",
        services=["онлайн-запись", "Telegram-бот"],
        target_pain="запись через личные сообщения",
        strict_match=False,
    )
    score, _ = score_lead(lead, request)
    assert score <= 70


def test_screenshot_batch_keeps_only_three_relevant_profiles():
    from app.services.search_quality import rank_search_results
    from app.services.search_service import SearchResult

    request = SearchRequest(
        niche="косметолог",
        city="Москва",
        target_pain="запись через личные сообщения",
        min_score=50,
        strict_match=False,
    )
    results = [
        SearchResult(
            "ТВОЙ КОСМЕТОЛОГ (@dr.beautysense) | TikTok",
            "https://www.tiktok.com/@dr.beautysense",
            "Косметолог Москва. Телефон +79774561757",
        ),
        SearchResult(
            "НАРАЩИВАНИЕ РЕСНИЦ! Косметолог! Москва!",
            "https://vk.com/public78610651",
            "Мастер по ресницам. Запись в сообщения.",
        ),
        SearchResult(
            "DR.BELCHIKOVA / КОСМЕТОЛОГ / Москва",
            "https://vk.com/cosmetolog_belchikova",
            "Косметолог Москва. Запись в сообщения.",
        ),
        SearchResult(
            "Кристина | косметолог | Москва",
            "https://www.facebook.com/makeup.studio.kristina/",
            "Log in or sign up to view",
        ),
        SearchResult(
            "Профиль @kosmetologlanamoskva",
            "https://www.instagram.com/kosmetologlanamoskva/",
            "Косметолог Москва. Телефон +7 968 013-72-20",
        ),
    ]
    ranked = rank_search_results(results, request, 20)
    titles = [item.title for item in ranked]
    assert len(ranked) == 3
    assert any("dr.beautysense" in title for title in titles)
    assert any("BELCHIKOVA" in title for title in titles)
    assert any("kosmetologlanamoskva" in title for title in titles)
    assert all("РЕСНИЦ" not in title for title in titles)
    assert all("Кристина" not in title for title in titles)

