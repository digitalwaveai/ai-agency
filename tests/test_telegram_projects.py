import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import User
from app.services.niche_profile_service import (
    create_user_project,
    save_project_answer,
    seed_niche_profiles,
)
from app.services.telegram_project_service import (
    delete_owned_project,
    first_unanswered_index,
    format_answer,
    format_project_card,
    get_owned_project,
    list_categories,
    list_profiles_for_category,
    list_user_projects,
    project_progress,
    reset_project_to_draft,
)
from app.telegram_projects import (
    BUTTON_NEW_PROJECT,
    BUTTON_PROJECTS,
    ProjectFlow,
    leadpilot_main_keyboard,
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
    seed_niche_profiles(session)
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def make_user(db, suffix="1"):
    user = User(
        public_id=f"00000000-0000-0000-0000-{suffix.zfill(12)}",
        display_name="Тест",
        status="active",
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_categories_and_profiles_are_available(db):
    categories = list_categories(db)
    assert len(categories) == 13
    beauty = list_profiles_for_category(db, "beauty")
    assert [item.code for item in beauty] == ["beauty_expert"]
    marketing = list_profiles_for_category(db, "marketing")
    assert {"marketing_strategy", "smm"}.issubset(
        {item.code for item in marketing}
    )


def test_list_projects_is_user_specific(db):
    first = make_user(db, "1")
    second = make_user(db, "2")
    create_user_project(
        db,
        user_id=first.id,
        name="Видео",
        profile_code="video_editing",
    )
    create_user_project(
        db,
        user_id=second.id,
        name="SMM",
        profile_code="smm",
    )
    assert [p.name for p in list_user_projects(db, first.id)] == ["Видео"]
    assert [p.name for p in list_user_projects(db, second.id)] == ["SMM"]


def test_owned_project_rejects_other_user(db):
    first = make_user(db, "1")
    second = make_user(db, "2")
    project = create_user_project(
        db,
        user_id=first.id,
        name="Проект",
        profile_code="software_development",
    )
    with pytest.raises(ValueError):
        get_owned_project(
            db,
            project_id=project.id,
            user_id=second.id,
        )


def test_delete_owned_project(db):
    user = make_user(db)
    project = create_user_project(
        db,
        user_id=user.id,
        name="Удаляемый",
        profile_code="marketing_strategy",
    )
    delete_owned_project(
        db,
        project_id=project.id,
        user_id=user.id,
    )
    assert list_user_projects(db, user.id) == []


def test_progress_and_first_unanswered():
    questionnaire = [
        {"key": "a", "required": True},
        {"key": "b", "required": False},
        {"key": "c", "required": True},
    ]
    answers = {"a": "ok"}
    assert project_progress(questionnaire, answers) == (1, 3)
    assert first_unanswered_index(questionnaire, answers) == 2


def test_project_card_contains_profile_and_progress(db):
    user = make_user(db)
    project = create_user_project(
        db,
        user_id=user.id,
        name="Монтаж экспертов",
        profile_code="video_editing",
    )
    save_project_answer(
        db,
        project_id=project.id,
        question_key="video_types",
        answer=["Shorts"],
    )
    text = format_project_card(db, project, include_answers=True)
    assert "Монтаж экспертов" in text
    assert "Видеомонтаж" in text
    assert "1 / 10" in text
    assert "Shorts" in text


def test_reset_project_to_draft(db):
    user = make_user(db)
    project = create_user_project(
        db,
        user_id=user.id,
        name="SMM",
        profile_code="smm",
    )
    project.status = "active"
    db.commit()
    reset = reset_project_to_draft(
        db,
        project_id=project.id,
        user_id=user.id,
    )
    assert reset.status == "draft"


def test_format_answer():
    assert format_answer(True) == "Да"
    assert format_answer(False) == "Нет"
    assert format_answer(["Telegram", "VK"]) == "Telegram, VK"
    assert format_answer(None) == "—"


def test_main_keyboard_contains_project_actions():
    keyboard = leadpilot_main_keyboard()
    texts = [
        button.text
        for row in keyboard.keyboard
        for button in row
    ]
    assert BUTTON_NEW_PROJECT in texts
    assert BUTTON_PROJECTS in texts
    assert "🔎 Найти клиентов" in texts


def test_project_flow_states_exist():
    assert ProjectFlow.waiting_project_name.state
    assert ProjectFlow.answering_text.state
    assert ProjectFlow.answering_choice.state
