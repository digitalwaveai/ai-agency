from __future__ import annotations

import html
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    NicheCategory,
    NicheProfile,
    ProjectAnswer,
    QuestionnaireQuestion,
    QuestionnaireTemplate,
    UserProject,
)
from app.services.niche_profile_service import (
    NicheProfileError,
    complete_project,
    get_niche_profile,
    get_project_answers,
    get_questionnaire,
)


@dataclass(frozen=True)
class CategoryItem:
    code: str
    name: str
    emoji: str
    profile_count: int


@dataclass(frozen=True)
class ProfileItem:
    code: str
    name: str
    description: str
    is_custom: bool


def list_categories(db: Session) -> list[CategoryItem]:
    categories = db.scalars(
        select(NicheCategory)
        .where(NicheCategory.is_active.is_(True))
        .order_by(NicheCategory.sort_order.asc(), NicheCategory.name.asc())
    ).all()

    result: list[CategoryItem] = []
    for category in categories:
        profiles = db.scalars(
            select(NicheProfile).where(
                NicheProfile.category_id == category.id,
                NicheProfile.is_active.is_(True),
            )
        ).all()
        if not profiles:
            continue
        result.append(
            CategoryItem(
                code=category.code,
                name=category.name,
                emoji=category.emoji or "📁",
                profile_count=len(profiles),
            )
        )
    return result


def list_profiles_for_category(
    db: Session,
    category_code: str,
) -> list[ProfileItem]:
    category = db.scalar(
        select(NicheCategory).where(
            NicheCategory.code == category_code,
            NicheCategory.is_active.is_(True),
        )
    )
    if category is None:
        return []

    profiles = db.scalars(
        select(NicheProfile)
        .where(
            NicheProfile.category_id == category.id,
            NicheProfile.is_active.is_(True),
        )
        .order_by(NicheProfile.is_custom.asc(), NicheProfile.name.asc())
    ).all()

    return [
        ProfileItem(
            code=profile.code,
            name=profile.name,
            description=profile.description,
            is_custom=profile.is_custom,
        )
        for profile in profiles
    ]


def list_user_projects(db: Session, user_id: int) -> list[UserProject]:
    return list(
        db.scalars(
            select(UserProject)
            .where(UserProject.user_id == user_id)
            .order_by(UserProject.updated_at.desc(), UserProject.id.desc())
        ).all()
    )


def get_owned_project(
    db: Session,
    *,
    project_id: int,
    user_id: int,
) -> UserProject:
    project = db.scalar(
        select(UserProject).where(
            UserProject.id == project_id,
            UserProject.user_id == user_id,
        )
    )
    if project is None:
        raise NicheProfileError("Проект не найден или недоступен")
    return project


def delete_owned_project(
    db: Session,
    *,
    project_id: int,
    user_id: int,
) -> None:
    project = get_owned_project(
        db,
        project_id=project_id,
        user_id=user_id,
    )
    db.delete(project)
    db.commit()


def reset_project_to_draft(
    db: Session,
    *,
    project_id: int,
    user_id: int,
) -> UserProject:
    project = get_owned_project(
        db,
        project_id=project_id,
        user_id=user_id,
    )
    project.status = "draft"
    db.commit()
    db.refresh(project)
    return project


def complete_owned_project(
    db: Session,
    *,
    project_id: int,
    user_id: int,
) -> UserProject:
    get_owned_project(
        db,
        project_id=project_id,
        user_id=user_id,
    )
    return complete_project(db, project_id)


def get_project_profile(
    db: Session,
    project: UserProject,
) -> NicheProfile:
    profile = db.get(NicheProfile, project.niche_profile_id)
    if profile is None:
        raise NicheProfileError("Профиль проекта не найден")
    return profile


def get_project_questionnaire(
    db: Session,
    project: UserProject,
) -> list[dict[str, Any]]:
    profile = get_project_profile(db, project)
    return get_questionnaire(db, profile.code)


def first_unanswered_index(
    questionnaire: list[dict[str, Any]],
    answers: dict[str, Any],
) -> int:
    for index, question in enumerate(questionnaire):
        if question["required"] and question["key"] not in answers:
            return index
    return len(questionnaire)


def project_progress(
    questionnaire: list[dict[str, Any]],
    answers: dict[str, Any],
) -> tuple[int, int]:
    answered = sum(1 for item in questionnaire if item["key"] in answers)
    return answered, len(questionnaire)


def decode_summary(project: UserProject) -> dict[str, Any]:
    if not project.summary_json:
        return {}
    try:
        value = json.loads(project.summary_json)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def format_answer(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "—"
    return str(value)


def status_label(status: str) -> str:
    return {
        "draft": "📝 Черновик",
        "active": "✅ Активен",
        "paused": "⏸ Приостановлен",
    }.get(status, status)


def format_project_card(
    db: Session,
    project: UserProject,
    *,
    include_answers: bool = False,
) -> str:
    profile = get_project_profile(db, project)
    questionnaire = get_questionnaire(db, profile.code)
    answers = get_project_answers(db, project.id)
    answered, total = project_progress(questionnaire, answers)

    lines = [
        f"📁 <b>{html.escape(project.name)}</b>",
        "",
        f"Ниша: <b>{html.escape(profile.name)}</b>",
        f"Статус: <b>{html.escape(status_label(project.status))}</b>",
        f"Анкета: <b>{answered} / {total}</b>",
    ]
    if project.custom_niche:
        lines.append(f"Направление: <b>{html.escape(project.custom_niche)}</b>")

    if include_answers and answers:
        labels = {item["key"]: item["label"] for item in questionnaire}
        lines.extend(["", "<b>Ответы:</b>"])
        for key, value in answers.items():
            label = labels.get(key, key)
            lines.append(f"• {html.escape(label)}: "f"<b>{html.escape(format_answer(value))}</b>")

    return "\n".join(lines)
