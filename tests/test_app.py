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
