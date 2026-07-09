import json

import pytest
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    NicheCategory,
    NicheProfile,
    ProjectAnswer,
    QuestionnaireQuestion,
    QuestionnaireTemplate,
    User,
    UserProject,
)
from app.services.niche_profile_service import (
    NicheProfileError,
    complete_project,
    create_user_project,
    get_niche_profile,
    get_project_answers,
    get_questionnaire,
    list_niche_profiles,
    save_project_answer,
    seed_niche_profiles,
)


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
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def make_user(db):
    user = User(
        public_id="00000000-0000-0000-0000-000000000001",
        display_name="Тестовый пользователь",
        status="active",
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_universal_tables_registered():
    expected = {
        "niche_categories",
        "niche_profiles",
        "questionnaire_templates",
        "questionnaire_questions",
        "user_projects",
        "project_answers",
    }
    assert expected.issubset(set(Base.metadata.tables))


def test_seed_creates_profiles_and_questions(db):
    result = seed_niche_profiles(db)
    assert result["categories"] == 7
    assert result["profiles"] == 8
    assert result["questions"] == 80
    assert db.scalar(select(func.count(NicheProfile.id))) == 8
    assert db.scalar(select(func.count(QuestionnaireQuestion.id))) == 80


def test_seed_is_idempotent(db):
    seed_niche_profiles(db)
    seed_niche_profiles(db)
    assert db.scalar(select(func.count(NicheCategory.id))) == 7
    assert db.scalar(select(func.count(NicheProfile.id))) == 8
    assert db.scalar(select(func.count(QuestionnaireTemplate.id))) == 8
    assert db.scalar(select(func.count(QuestionnaireQuestion.id))) == 80


def test_each_profile_has_its_own_questionnaire(db):
    seed_niche_profiles(db)
    beauty = get_questionnaire(db, "beauty_expert")
    trading = get_questionnaire(db, "trading_finance_content")
    marketing = get_questionnaire(db, "marketing_strategy")
    smm = get_questionnaire(db, "smm")
    development = get_questionnaire(db, "software_development")
    video = get_questionnaire(db, "video_editing")
    cards = get_questionnaire(db, "marketplace_card_design")

    assert len(beauty) == 10
    assert len(trading) == 10
    assert len(marketing) == 10
    assert len(smm) == 10
    assert len(development) == 10
    assert len(video) == 10
    assert len(cards) == 10

    assert beauty[0]["key"] == "specialization"
    assert trading[0]["key"] == "offer_type"
    assert smm[0]["key"] == "platforms"
    assert development[0]["key"] == "product_type"
    assert video[0]["key"] == "video_types"
    assert cards[0]["key"] == "marketplaces"


def test_custom_profile_exists(db):
    seed_niche_profiles(db)
    profile = get_niche_profile(db, "custom_niche")
    assert profile is not None
    assert profile.is_custom is True
    questionnaire = get_questionnaire(db, "custom_niche")
    assert questionnaire[0]["key"] == "niche_name"


def test_profile_configs_have_different_pain_signals(db):
    seed_niche_profiles(db)
    beauty = get_niche_profile(db, "beauty_expert")
    development = get_niche_profile(db, "software_development")
    assert beauty is not None
    assert development is not None
    beauty_config = json.loads(beauty.config_json)
    development_config = json.loads(development.config_json)
    assert beauty_config["pain_signals"] != development_config["pain_signals"]


def test_create_project_and_save_answers(db):
    seed_niche_profiles(db)
    user = make_user(db)
    project = create_user_project(
        db,
        user_id=user.id,
        name="Монтаж для экспертов",
        profile_code="video_editing",
    )
    save_project_answer(
        db,
        project_id=project.id,
        question_key="video_types",
        answer=["Shorts", "YouTube"],
    )
    answers = get_project_answers(db, project.id)
    assert answers["video_types"] == ["Shorts", "YouTube"]
    assert db.scalar(select(func.count(ProjectAnswer.id))) == 1


def test_answer_upsert_does_not_duplicate(db):
    seed_niche_profiles(db)
    user = make_user(db)
    project = create_user_project(
        db,
        user_id=user.id,
        name="SMM",
        profile_code="smm",
    )
    first = save_project_answer(
        db,
        project_id=project.id,
        question_key="platforms",
        answer=["Telegram"],
    )
    second = save_project_answer(
        db,
        project_id=project.id,
        question_key="platforms",
        answer=["Telegram", "VK"],
    )
    assert first.id == second.id
    assert db.scalar(select(func.count(ProjectAnswer.id))) == 1
    assert get_project_answers(db, project.id)["platforms"] == ["Telegram", "VK"]


def test_custom_project_requires_custom_niche(db):
    seed_niche_profiles(db)
    user = make_user(db)
    with pytest.raises(NicheProfileError, match="укажите название"):
        create_user_project(
            db,
            user_id=user.id,
            name="Своя ниша",
            profile_code="custom_niche",
        )


def test_complete_project_requires_all_required_answers(db):
    seed_niche_profiles(db)
    user = make_user(db)
    project = create_user_project(
        db,
        user_id=user.id,
        name="Beauty",
        profile_code="beauty_expert",
    )
    with pytest.raises(NicheProfileError, match="Не заполнены"):
        complete_project(db, project.id)


def test_complete_project_after_full_questionnaire(db):
    seed_niche_profiles(db)
    user = make_user(db)
    project = create_user_project(
        db,
        user_id=user.id,
        name="Карточки Ozon",
        profile_code="marketplace_card_design",
    )
    questionnaire = get_questionnaire(db, "marketplace_card_design")
    for item in questionnaire:
        answer = ["test"] if item["type"] == "multiple_choice" else "test"
        if item["type"] == "number":
            answer = 10
        if item["type"] == "boolean":
            answer = True
        save_project_answer(
            db,
            project_id=project.id,
            question_key=item["key"],
            answer=answer,
        )
    completed = complete_project(db, project.id)
    assert completed.status == "active"
    summary = json.loads(completed.summary_json)
    assert summary["profile_code"] == "marketplace_card_design"


def test_list_profiles_returns_custom_last(db):
    seed_niche_profiles(db)
    profiles = list_niche_profiles(db)
    assert len(profiles) == 8
    assert profiles[-1].code == "custom_niche"
