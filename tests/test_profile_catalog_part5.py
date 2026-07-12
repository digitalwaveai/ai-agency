import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import NicheCategory, NicheProfile, QuestionnaireQuestion
from app.schemas import LeadCreate, SearchRequest
from app.services.niche_profile_catalog_part5 import (
    CATEGORIES_PART5,
    PAIN_RULE_SPECS_PART5,
    PROFILE_SPECS_PART5,
)
from app.services.niche_profile_service import (
    get_niche_profile,
    get_questionnaire,
    seed_niche_profiles,
)
from app.services.universal_insight_service import analyze_universal_lead


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


@pytest.fixture()
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def make_lead(description: str) -> LeadCreate:
    return LeadCreate(
        name="Тестовая компания",
        niche="бизнес",
        city="Москва",
        description=description,
        source_url="https://example.com/company",
        score=0,
    )


def make_request(
    niche: str,
    target_type: str,
    target_pain: str,
    services: list[str],
) -> SearchRequest:
    return SearchRequest(
        niche=niche,
        city="Москва",
        target_type=target_type,
        target_pain=target_pain,
        services=services,
        strict_match=False,
    )


def test_part5_catalog_has_31_unique_profiles():
    codes = [item["code"] for item in PROFILE_SPECS_PART5]
    assert len(PROFILE_SPECS_PART5) == 31
    assert len(codes) == len(set(codes))
    assert len(CATEGORIES_PART5) == 6
    assert set(codes) == set(PAIN_RULE_SPECS_PART5)


def test_seed_creates_complete_expanded_catalog(db):
    result = seed_niche_profiles(db)
    assert result == {
        "categories": 13,
        "profiles": 39,
        "questions": 390,
    }
    assert db.scalar(select(func.count(NicheCategory.id))) == 13
    assert db.scalar(select(func.count(NicheProfile.id))) == 39
    assert db.scalar(select(func.count(QuestionnaireQuestion.id))) == 390


def test_seed_remains_idempotent_with_part5(db):
    seed_niche_profiles(db)
    seed_niche_profiles(db)
    assert db.scalar(select(func.count(NicheCategory.id))) == 13
    assert db.scalar(select(func.count(NicheProfile.id))) == 39
    assert db.scalar(select(func.count(QuestionnaireQuestion.id))) == 390


@pytest.mark.parametrize(
    "profile_code",
    [
        "ai_automation",
        "telegram_bots",
        "web_development",
        "ui_ux_design",
        "targeted_ads",
        "seo",
        "copywriting",
        "tutoring",
        "legal_services",
        "marketplace_management",
        "restaurants_cafes",
        "real_estate",
    ],
)
def test_new_profiles_have_individual_ten_question_forms(db, profile_code):
    seed_niche_profiles(db)
    profile = get_niche_profile(db, profile_code)
    assert profile is not None
    questionnaire = get_questionnaire(db, profile_code)
    assert len(questionnaire) == 10
    assert questionnaire[0]["key"] == "specialization"
    assert questionnaire[1]["key"] == "target_type"
    assert questionnaire[-1]["key"] == "entry_offer"


def test_every_part5_profile_has_search_and_offer_config(db):
    seed_niche_profiles(db)
    for item in PROFILE_SPECS_PART5:
        profile = get_niche_profile(db, item["code"])
        assert profile is not None
        config = json.loads(profile.config_json)
        assert config["positive_keywords"]
        assert config["default_exclusions"]
        assert config["pain_signals"]
        assert config["offer_examples"]


@pytest.mark.parametrize(
    ("profile_code", "description", "expected_pain", "expected_offer"),
    [
        (
            "ai_automation",
            "Все заявки обрабатываются вручную менеджером.",
            "Заявки обрабатываются вручную",
            "автоматизация",
        ),
        (
            "seo",
            "Сайт почти не виден в поиске и получает мало трафика.",
            "Сайт не виден в поиске",
            "SEO-аудит",
        ),
        (
            "legal_services",
            "Наши договоры не стандартизированы и каждый составляется заново.",
            "Договоры не стандартизированы",
            "договор",
        ),
        (
            "restaurants_cafes",
            "Бронирование столов принимается вручную через сообщения.",
            "Бронирование принимается вручную",
            "бронирования",
        ),
        (
            "renovation_construction",
            "Сметы готовятся долго, клиент ждёт несколько дней.",
            "Сметы готовятся слишком долго",
            "смет",
        ),
    ],
)
def test_part5_explicit_pain_rules(
    profile_code,
    description,
    expected_pain,
    expected_offer,
):
    profile = next(
        item
        for item in PROFILE_SPECS_PART5
        if item["code"] == profile_code
    )
    lead = make_lead(description)
    request = make_request(
        niche=profile["name"],
        target_type=profile["target_options"][0],
        target_pain=profile["pain_signals"][0],
        services=profile["offer_examples"],
    )
    result = analyze_universal_lead(
        lead,
        request,
        {
            "profile_code": profile_code,
            "profile_name": profile["name"],
            "custom_niche": "",
            "pain_signals": profile["pain_signals"],
            "offer_examples": profile["offer_examples"],
        },
    )
    assert expected_pain in result.pain_points
    assert "Подтверждение:" in result.pain_points
    assert expected_offer.lower() in result.suggested_offer.lower()


def test_all_part5_questionnaires_are_not_empty(db):
    seed_niche_profiles(db)
    for item in PROFILE_SPECS_PART5:
        questions = get_questionnaire(db, item["code"])
        labels = [question["label"] for question in questions]
        assert len(questions) == 10
        assert len(set(labels)) == 10
        assert all(label.strip() for label in labels)
