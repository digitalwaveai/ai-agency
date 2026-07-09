import json

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Lead, ProjectLead, User
from app.schemas import SearchRequest
from app.services.niche_profile_service import (
    create_user_project,
    save_project_answer,
    seed_niche_profiles,
)
from app.services.project_search_service import (
    build_project_search_request,
    get_saved_search_location,
    link_project_lead,
    list_active_projects,
    list_recent_user_leads,
    parse_location,
    save_search_location,
)
from app.services.universal_query_builder import build_search_queries
from app.telegram_search import format_lead_card


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture()
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    seed_niche_profiles(session)
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def make_user(db):
    user = User(
        public_id="00000000-0000-0000-0000-000000000099",
        display_name="Тест",
        status="active",
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_project_leads_table_registered():
    assert "project_leads" in Base.metadata.tables


def test_parse_location():
    assert parse_location("Москва") == ("Москва", "")
    assert parse_location("Казань, Россия") == ("Казань", "Россия")
    assert parse_location("") == ("онлайн", "")


def test_generic_queries_are_not_beauty_specific():
    req = SearchRequest(
        niche="онлайн-школы",
        city="Москва",
        target_type="эксперты, онлайн-школы",
        target_pain="нет коротких видео",
        exclude="крупные сети",
        strict_match=False,
    )
    queries = build_search_queries(req)
    assert any("онлайн-школы" in item for item in queries)
    assert any("site:t.me" in item for item in queries)
    assert all("частный мастер" not in item for item in queries)


def test_beauty_queries_keep_existing_behavior():
    req = SearchRequest(
        niche="косметолог",
        city="Москва",
        services=["сайт"],
    )
    queries = build_search_queries(req)
    assert any("косметолог Москва" in item for item in queries)
    assert any("WhatsApp" in item for item in queries)


def test_build_smm_project_request(db):
    user = make_user(db)
    project = create_user_project(
        db,
        user_id=user.id,
        name="SMM для онлайн-школ",
        profile_code="smm",
    )
    save_project_answer(
        db,
        project_id=project.id,
        question_key="target_type",
        answer=["Онлайн-школы", "Эксперты"],
    )
    save_project_answer(
        db,
        project_id=project.id,
        question_key="content_types",
        answer=["Reels", "Shorts"],
    )
    save_project_answer(
        db,
        project_id=project.id,
        question_key="priority_pains",
        answer=["Нет коротких видео", "Нерегулярные публикации"],
    )
    req = build_project_search_request(
        db,
        project=project,
        location="Москва, Россия",
        limit=5,
    )
    assert req.niche == "Онлайн-школы"
    assert req.city == "Москва"
    assert req.country == "Россия"
    assert "Shorts" in req.services
    assert "Нет коротких видео" in req.target_pain
    assert req.strict_match is False


def test_custom_project_uses_target_customer(db):
    user = make_user(db)
    project = create_user_project(
        db,
        user_id=user.id,
        name="Кофемашины",
        profile_code="custom_niche",
        custom_niche="Ремонт кофемашин",
    )
    save_project_answer(
        db,
        project_id=project.id,
        question_key="target_customer",
        answer="Рестораны и кофейни",
    )
    save_project_answer(
        db,
        project_id=project.id,
        question_key="service",
        answer="Ремонт и обслуживание",
    )
    req = build_project_search_request(
        db,
        project=project,
        location="Санкт-Петербург",
        limit=3,
    )
    assert req.niche == "Рестораны и кофейни"
    assert req.services == ["Ремонт и обслуживание"]


def test_active_projects_only(db):
    user = make_user(db)
    draft = create_user_project(
        db,
        user_id=user.id,
        name="Черновик",
        profile_code="video_editing",
    )
    active = create_user_project(
        db,
        user_id=user.id,
        name="Активный",
        profile_code="marketing_strategy",
    )
    active.status = "active"
    db.commit()
    projects = list_active_projects(db, user_id=user.id)
    assert [item.id for item in projects] == [active.id]
    assert draft.id not in [item.id for item in projects]


def test_search_location_is_saved(db):
    user = make_user(db)
    project = create_user_project(
        db,
        user_id=user.id,
        name="Регион",
        profile_code="software_development",
    )
    save_search_location(
        db,
        project_id=project.id,
        location="Казань",
    )
    assert get_saved_search_location(db, project_id=project.id) == "Казань"


def test_project_lead_link_is_idempotent(db):
    user = make_user(db)
    project = create_user_project(
        db,
        user_id=user.id,
        name="Проект",
        profile_code="video_editing",
    )
    lead = Lead(
        name="Эксперт",
        niche="эксперт",
        city="Москва",
        source_url="https://example.com/expert",
        score=70,
        status="new",
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    first = link_project_lead(
        db,
        user_id=user.id,
        project_id=project.id,
        lead_id=lead.id,
        search_run_id=None,
    )
    second = link_project_lead(
        db,
        user_id=user.id,
        project_id=project.id,
        lead_id=lead.id,
        search_run_id=None,
    )
    assert first.id == second.id
    rows = list_recent_user_leads(db, user_id=user.id)
    assert len(rows) == 1
    assert rows[0][1].id == lead.id


def test_format_lead_card():
    lead = Lead(
        name="Онлайн-школа",
        niche="образование",
        city="Москва",
        source_url="https://example.com/school",
        score=82,
        pain_points="Нет коротких видео",
        suggested_offer="Пакет Shorts",
        status="new",
    )
    text = format_lead_card(lead, project_name="Монтаж")
    assert "Онлайн-школа" in text
    assert "82/100" in text
    assert "Нет коротких видео" in text
    assert "Пакет Shorts" in text
    assert "Открыть источник" in text
