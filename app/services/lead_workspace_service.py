from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Lead,
    LeadActivity,
    LeadWorkspace,
    ProjectLead,
    UserProject,
)


PIPELINE_STATUSES = (
    "found",
    "saved",
    "contacted",
    "replied",
    "qualified",
    "won",
    "lost",
)

STATUS_LABELS = {
    "found": "Новый",
    "saved": "Сохранён",
    "contacted": "Связались",
    "replied": "Ответил",
    "qualified": "Квалифицирован",
    "won": "Сделка",
    "lost": "Отказ",
}

CLOSED_STATUSES = {"won", "lost"}


class LeadWorkspaceError(ValueError):
    pass


@dataclass(frozen=True)
class PipelineLeadRow:
    project_lead: ProjectLead
    lead: Lead
    project: UserProject
    workspace: LeadWorkspace | None


def status_label(status: str | None) -> str:
    return STATUS_LABELS.get(str(status or ""), str(status or "Новый"))


def _owned_row(
    db: Session,
    *,
    user_id: int,
    project_lead_id: int,
) -> PipelineLeadRow:
    result = db.execute(
        select(ProjectLead, Lead, UserProject, LeadWorkspace)
        .join(Lead, Lead.id == ProjectLead.lead_id)
        .join(UserProject, UserProject.id == ProjectLead.project_id)
        .outerjoin(
            LeadWorkspace,
            LeadWorkspace.project_lead_id == ProjectLead.id,
        )
        .where(
            ProjectLead.id == project_lead_id,
            ProjectLead.user_id == user_id,
        )
    ).one_or_none()

    if result is None:
        raise LeadWorkspaceError("Лид не найден или принадлежит другому пользователю")

    project_lead, lead, project, workspace = result
    return PipelineLeadRow(
        project_lead=project_lead,
        lead=lead,
        project=project,
        workspace=workspace,
    )


def get_pipeline_lead(
    db: Session,
    *,
    user_id: int,
    project_lead_id: int,
) -> PipelineLeadRow:
    return _owned_row(
        db,
        user_id=user_id,
        project_lead_id=project_lead_id,
    )


def _get_or_create_workspace(
    db: Session,
    *,
    user_id: int,
    project_lead_id: int,
) -> LeadWorkspace:
    row = db.scalar(
        select(LeadWorkspace).where(
            LeadWorkspace.project_lead_id == project_lead_id,
            LeadWorkspace.user_id == user_id,
        )
    )
    if row is None:
        row = LeadWorkspace(
            user_id=user_id,
            project_lead_id=project_lead_id,
            note="",
        )
        db.add(row)
        db.flush()
    return row


def _add_activity(
    db: Session,
    *,
    user_id: int,
    project_lead_id: int,
    activity_type: str,
    old_value: str | None,
    new_value: str | None,
) -> LeadActivity:
    activity = LeadActivity(
        user_id=user_id,
        project_lead_id=project_lead_id,
        activity_type=activity_type,
        old_value=old_value,
        new_value=new_value,
    )
    db.add(activity)
    return activity


def update_pipeline_status(
    db: Session,
    *,
    user_id: int,
    project_lead_id: int,
    status: str,
    now: datetime | None = None,
) -> PipelineLeadRow:
    if status not in PIPELINE_STATUSES:
        raise LeadWorkspaceError(f"Неизвестный статус: {status}")

    now = now or datetime.utcnow()
    row = _owned_row(
        db,
        user_id=user_id,
        project_lead_id=project_lead_id,
    )
    old_status = row.project_lead.status

    row.project_lead.status = status
    row.project_lead.updated_at = now

    workspace = _get_or_create_workspace(
        db,
        user_id=user_id,
        project_lead_id=project_lead_id,
    )
    if status == "contacted":
        workspace.last_contacted_at = now
    workspace.updated_at = now

    _add_activity(
        db,
        user_id=user_id,
        project_lead_id=project_lead_id,
        activity_type="status_changed",
        old_value=old_status,
        new_value=status,
    )

    db.commit()
    return _owned_row(
        db,
        user_id=user_id,
        project_lead_id=project_lead_id,
    )


def save_lead_note(
    db: Session,
    *,
    user_id: int,
    project_lead_id: int,
    note: str,
    now: datetime | None = None,
) -> PipelineLeadRow:
    cleaned = " ".join((note or "").split()).strip()
    if not cleaned:
        raise LeadWorkspaceError("Заметка не может быть пустой")
    if len(cleaned) > 2000:
        raise LeadWorkspaceError("Заметка слишком длинная. Максимум 2000 символов")

    now = now or datetime.utcnow()
    _owned_row(
        db,
        user_id=user_id,
        project_lead_id=project_lead_id,
    )
    workspace = _get_or_create_workspace(
        db,
        user_id=user_id,
        project_lead_id=project_lead_id,
    )
    old_note = workspace.note
    workspace.note = cleaned
    workspace.updated_at = now

    _add_activity(
        db,
        user_id=user_id,
        project_lead_id=project_lead_id,
        activity_type="note_changed",
        old_value=old_note,
        new_value=cleaned,
    )

    db.commit()
    return _owned_row(
        db,
        user_id=user_id,
        project_lead_id=project_lead_id,
    )


def schedule_follow_up(
    db: Session,
    *,
    user_id: int,
    project_lead_id: int,
    follow_up_at: datetime | None,
    now: datetime | None = None,
) -> PipelineLeadRow:
    now = now or datetime.utcnow()
    _owned_row(
        db,
        user_id=user_id,
        project_lead_id=project_lead_id,
    )
    workspace = _get_or_create_workspace(
        db,
        user_id=user_id,
        project_lead_id=project_lead_id,
    )
    old_value = (
        workspace.next_follow_up_at.isoformat()
        if workspace.next_follow_up_at
        else None
    )
    workspace.next_follow_up_at = follow_up_at
    workspace.updated_at = now

    _add_activity(
        db,
        user_id=user_id,
        project_lead_id=project_lead_id,
        activity_type="follow_up_changed",
        old_value=old_value,
        new_value=follow_up_at.isoformat() if follow_up_at else None,
    )

    db.commit()
    return _owned_row(
        db,
        user_id=user_id,
        project_lead_id=project_lead_id,
    )


def list_pipeline_leads(
    db: Session,
    *,
    user_id: int,
    status: str | None = None,
    project_id: int | None = None,
    limit: int = 50,
) -> list[PipelineLeadRow]:
    statement = (
        select(ProjectLead, Lead, UserProject, LeadWorkspace)
        .join(Lead, Lead.id == ProjectLead.lead_id)
        .join(UserProject, UserProject.id == ProjectLead.project_id)
        .outerjoin(
            LeadWorkspace,
            LeadWorkspace.project_lead_id == ProjectLead.id,
        )
        .where(ProjectLead.user_id == user_id)
    )

    if status:
        if status not in PIPELINE_STATUSES:
            raise LeadWorkspaceError(f"Неизвестный статус: {status}")
        statement = statement.where(ProjectLead.status == status)

    if project_id is not None:
        statement = statement.where(ProjectLead.project_id == project_id)

    statement = statement.order_by(
        ProjectLead.updated_at.desc(),
        ProjectLead.id.desc(),
    ).limit(max(1, min(int(limit), 500)))

    return [
        PipelineLeadRow(
            project_lead=project_lead,
            lead=lead,
            project=project,
            workspace=workspace,
        )
        for project_lead, lead, project, workspace
        in db.execute(statement).all()
    ]


def pipeline_counts(
    db: Session,
    *,
    user_id: int,
) -> dict[str, int]:
    result = {status: 0 for status in PIPELINE_STATUSES}
    rows = db.execute(
        select(ProjectLead.status, func.count(ProjectLead.id))
        .where(ProjectLead.user_id == user_id)
        .group_by(ProjectLead.status)
    ).all()

    for status, count in rows:
        result[str(status)] = int(count)
    return result


def list_due_follow_ups(
    db: Session,
    *,
    user_id: int,
    now: datetime | None = None,
    limit: int = 50,
) -> list[PipelineLeadRow]:
    now = now or datetime.utcnow()
    statement = (
        select(ProjectLead, Lead, UserProject, LeadWorkspace)
        .join(Lead, Lead.id == ProjectLead.lead_id)
        .join(UserProject, UserProject.id == ProjectLead.project_id)
        .join(
            LeadWorkspace,
            LeadWorkspace.project_lead_id == ProjectLead.id,
        )
        .where(
            ProjectLead.user_id == user_id,
            LeadWorkspace.next_follow_up_at.is_not(None),
            LeadWorkspace.next_follow_up_at <= now,
            ProjectLead.status.notin_(CLOSED_STATUSES),
        )
        .order_by(LeadWorkspace.next_follow_up_at.asc())
        .limit(max(1, min(int(limit), 200)))
    )

    return [
        PipelineLeadRow(
            project_lead=project_lead,
            lead=lead,
            project=project,
            workspace=workspace,
        )
        for project_lead, lead, project, workspace
        in db.execute(statement).all()
    ]


def list_activities(
    db: Session,
    *,
    user_id: int,
    project_lead_id: int,
    limit: int = 20,
) -> list[LeadActivity]:
    _owned_row(
        db,
        user_id=user_id,
        project_lead_id=project_lead_id,
    )
    return list(
        db.scalars(
            select(LeadActivity)
            .where(
                LeadActivity.user_id == user_id,
                LeadActivity.project_lead_id == project_lead_id,
            )
            .order_by(LeadActivity.created_at.desc(), LeadActivity.id.desc())
            .limit(max(1, min(int(limit), 100)))
        ).all()
    )


def _source_url(lead: Lead) -> str:
    for value in (
        lead.website_url,
        lead.telegram_url,
        lead.instagram_url,
        lead.vk_url,
        lead.youtube_url,
        lead.source_url,
    ):
        if value:
            return str(value)
    return ""


def export_pipeline_csv(
    rows: Iterable[PipelineLeadRow],
) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(
        output,
        delimiter=";",
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
    )
    writer.writerow(
        [
            "Проект",
            "Лид",
            "Статус",
            "Рейтинг",
            "Боль",
            "Оффер",
            "Телефон",
            "Email",
            "WhatsApp",
            "Источник",
            "Заметка",
            "Следующий контакт",
            "Найден",
            "Обновлён",
        ]
    )

    for row in rows:
        workspace = row.workspace
        writer.writerow(
            [
                row.project.name,
                row.lead.name,
                status_label(row.project_lead.status),
                int(row.lead.score or 0),
                row.lead.pain_points or "",
                row.lead.suggested_offer or "",
                row.lead.phone or "",
                row.lead.email or "",
                row.lead.whatsapp or "",
                _source_url(row.lead),
                workspace.note if workspace else "",
                (
                    workspace.next_follow_up_at.strftime("%d.%m.%Y %H:%M")
                    if workspace and workspace.next_follow_up_at
                    else ""
                ),
                (
                    row.project_lead.found_at.strftime("%d.%m.%Y %H:%M")
                    if row.project_lead.found_at
                    else ""
                ),
                (
                    row.project_lead.updated_at.strftime("%d.%m.%Y %H:%M")
                    if row.project_lead.updated_at
                    else ""
                ),
            ]
        )

    return output.getvalue().encode("utf-8-sig")
