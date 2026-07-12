from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Lead, ProjectLead, UserLead, UserProject
from app.schemas import SearchRequest
from app.services.analytics_service import (
    finish_search_run,
    log_analytics_event,
    start_search_run,
)
from app.services.lead_pipeline import search_and_save_leads
from app.services.niche_profile_service import (
    NicheProfileError,
    get_project_answers,
    save_project_answer,
)
from app.services.telegram_project_service import (
    get_owned_project,
    get_project_profile,
)
from app.services.usage_service import (
    UsageReservation,
    confirm_usage,
    release_usage,
    reserve_user_usage,
)
from app.services.user_lead_service import save_user_lead


@dataclass(frozen=True)
class ProjectSearchExecution:
    project: UserProject
    request: SearchRequest
    leads: list[Lead]
    duration_ms: int
    search_run_id: int
    insight_context: dict[str, Any]


def _load_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        result = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default
    return result


def _as_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]
    text = str(value).strip()
    return [text] if text else []


def _collect_answers(
    answers: dict[str, Any],
    keys: tuple[str, ...],
) -> list[str]:
    result: list[str] = []
    for key in keys:
        result.extend(_as_items(answers.get(key)))
    return list(dict.fromkeys(result))


def _first_useful(values: list[str], fallback: str) -> str:
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned.lower() not in {
            "другое",
            "оба",
            "оба варианта",
        }:
            return cleaned
    return fallback


def parse_location(value: str) -> tuple[str, str]:
    cleaned = " ".join((value or "").split()).strip()
    if not cleaned:
        return "онлайн", ""

    parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    if len(parts) >= 2:
        return parts[0], ", ".join(parts[1:])
    return cleaned, ""


def build_project_search_request(
    db: Session,
    *,
    project: UserProject,
    location: str,
    limit: int = 5,
) -> SearchRequest:
    profile = get_project_profile(db, project)
    answers = get_project_answers(db, project.id)
    config = _load_json(profile.config_json, {})
    if not isinstance(config, dict):
        config = {}

    target_values = _collect_answers(
        answers,
        (
            "target_services",
            "target_customer",
            "target_business",
            "target_audience",
            "target_sellers",
            "target_type",
        ),
    )

    if profile.code == "marketplace_card_design":
        categories = _collect_answers(answers, ("product_categories",))
        target_values = [
            *categories,
            *target_values,
        ]

    target_fallback = project.custom_niche or profile.target_label or profile.name
    niche = _first_useful(target_values, target_fallback)
    target_type = ", ".join(target_values[:4]) or target_fallback

    services = _collect_answers(
        answers,
        (
            "service",
            "offer_type",
            "product_type",
            "content_types",
            "video_types",
            "specialization",
            "engagement_type",
            "service_format",
            "entry_offer",
            "additional_services",
        ),
    )
    if not services:
        services = _as_items(config.get("offer_examples"))

    pain_values = _collect_answers(
        answers,
        (
            "priority_pains",
            "required_signals",
            "required_evidence",
        ),
    )
    if not pain_values:
        pain_values = _as_items(config.get("pain_signals"))

    exclusions = _collect_answers(
        answers,
        (
            "exclusions",
            "excluded_industries",
            "excluded_tech",
            "excluded_audience",
        ),
    )
    exclusions.extend(_as_items(config.get("default_exclusions")))
    exclusions = list(dict.fromkeys(exclusions))

    city, country = parse_location(location)
    required_contacts = _collect_answers(answers, ("required_contacts",))

    return SearchRequest(
        niche=niche,
        city=city,
        country=country,
        language="ru",
        target_type=target_type,
        services=services[:8],
        target_pain=", ".join(pain_values[:5]),
        limit=max(1, min(int(limit), 10)),
        min_score=35,
        contacts_only=bool(required_contacts),
        exclude=", ".join(exclusions),
        strict_match=False,
    )



def build_project_insight_context(
    db: Session,
    *,
    project: UserProject,
) -> dict[str, Any]:
    profile = get_project_profile(db, project)
    answers = get_project_answers(db, project.id)
    config = _load_json(profile.config_json, {})
    if not isinstance(config, dict):
        config = {}

    return {
        "project_id": project.id,
        "project_name": project.name,
        "profile_code": profile.code,
        "profile_name": profile.name,
        "custom_niche": project.custom_niche or "",
        "seller_label": profile.seller_label,
        "target_label": profile.target_label,
        "pain_signals": [
            str(item)
            for item in config.get("pain_signals", [])
            if str(item).strip()
        ],
        "offer_examples": [
            str(item)
            for item in config.get("offer_examples", [])
            if str(item).strip()
        ],
        "answers": answers,
    }

def list_active_projects(
    db: Session,
    *,
    user_id: int,
) -> list[UserProject]:
    return list(
        db.scalars(
            select(UserProject)
            .where(
                UserProject.user_id == user_id,
                UserProject.status == "active",
            )
            .order_by(UserProject.updated_at.desc(), UserProject.id.desc())
        ).all()
    )


def get_saved_search_location(
    db: Session,
    *,
    project_id: int,
) -> str | None:
    answers = get_project_answers(db, project_id)
    value = answers.get("_search_location")
    return str(value).strip() if value else None


def save_search_location(
    db: Session,
    *,
    project_id: int,
    location: str,
) -> None:
    save_project_answer(
        db,
        project_id=project_id,
        question_key="_search_location",
        answer=location.strip(),
    )


def link_project_lead(
    db: Session,
    *,
    user_id: int,
    project_id: int,
    lead_id: int,
    search_run_id: int | None,
    now: datetime | None = None,
) -> ProjectLead:
    now = now or datetime.utcnow()
    row = db.scalar(
        select(ProjectLead).where(
            ProjectLead.project_id == project_id,
            ProjectLead.lead_id == lead_id,
        )
    )
    if row is None:
        row = ProjectLead(
            user_id=user_id,
            project_id=project_id,
            lead_id=lead_id,
            status="found",
            search_run_id=search_run_id,
            found_at=now,
            last_seen_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.user_id = user_id
        row.status = "found"
        row.search_run_id = search_run_id
        row.last_seen_at = now
        row.updated_at = now
    db.commit()
    db.refresh(row)
    return row


def list_recent_user_leads(
    db: Session,
    *,
    user_id: int,
    limit: int = 10,
) -> list[tuple[ProjectLead, Lead, UserProject]]:
    return list(
        db.execute(
            select(ProjectLead, Lead, UserProject)
            .join(Lead, Lead.id == ProjectLead.lead_id)
            .join(UserProject, UserProject.id == ProjectLead.project_id)
            .where(ProjectLead.user_id == user_id)
            .order_by(ProjectLead.updated_at.desc())
            .limit(max(1, min(limit, 50)))
        ).all()
    )


async def run_project_search(
    db: Session,
    *,
    user_id: int,
    project_id: int,
    location: str,
    limit: int,
    external_user_id: str | int | None = None,
    username: str | None = None,
    session_id: str | None = None,
) -> ProjectSearchExecution:
    project = get_owned_project(
        db,
        project_id=project_id,
        user_id=user_id,
    )
    if project.status != "active":
        raise NicheProfileError("Сначала завершите и активируйте проект")

    request = build_project_search_request(
        db,
        project=project,
        location=location,
        limit=limit,
    )
    insight_context = build_project_insight_context(
        db,
        project=project,
    )
    save_search_location(
        db,
        project_id=project.id,
        location=location,
    )

    reservation: UsageReservation | None = None
    started = time.perf_counter()
    search_run = start_search_run(
        db,
        platform="telegram",
        user_id=user_id,
        niche=request.niche,
        city=request.city,
        parameters={
            "project_id": project.id,
            "profile_id": project.niche_profile_id,
            "request": request.model_dump(),
        },
    )

    try:
        reservation = reserve_user_usage(
            db,
            user_id=user_id,
            resource="searches",
            amount=1,
            reason=f"Telegram project search: {project.id}",
            idempotency_key=f"telegram-search:{user_id}:{uuid.uuid4().hex}",
            reservation_minutes=30,
        )

        leads = await search_and_save_leads(
            request,
            db,
            insight_context=insight_context,
        )

        for lead in leads:
            save_user_lead(
                db,
                user_id=user_id,
                lead_id=lead.id,
                status="found",
            )
            link_project_lead(
                db,
                user_id=user_id,
                project_id=project.id,
                lead_id=lead.id,
                search_run_id=search_run.id,
            )

        confirm_usage(db, reservation)
        duration_ms = int((time.perf_counter() - started) * 1000)

        finish_search_run(
            db,
            search_run_id=search_run.id,
            status="completed",
            result_count=len(leads),
            duration_ms=duration_ms,
        )
        log_analytics_event(
            db,
            platform="telegram",
            event_name="project_search_completed",
            user_id=user_id,
            external_user_id=external_user_id,
            username=username,
            command_name="search_clients",
            parameters={
                "project_id": project.id,
                "location": location,
                "limit": limit,
                "niche": request.niche,
            },
            result_count=len(leads),
            duration_ms=duration_ms,
            session_id=session_id,
        )
        return ProjectSearchExecution(
            project=project,
            request=request,
            leads=leads,
            duration_ms=duration_ms,
            search_run_id=search_run.id,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        if reservation is not None:
            try:
                release_usage(
                    db,
                    reservation,
                    reason="Возврат из-за технической ошибки поиска",
                )
            except Exception:
                db.rollback()

        try:
            finish_search_run(
                db,
                search_run_id=search_run.id,
                status="failed",
                result_count=0,
                duration_ms=duration_ms,
                error_message=str(exc),
            )
            log_analytics_event(
                db,
                platform="telegram",
                event_name="project_search_failed",
                user_id=user_id,
                external_user_id=external_user_id,
                username=username,
                command_name="search_clients",
                parameters={
                    "project_id": project.id,
                    "location": location,
                    "limit": limit,
                },
                status="error",
                duration_ms=duration_ms,
                error_message=str(exc),
                session_id=session_id,
            )
        except Exception:
            db.rollback()
        raise
