from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Lead, User, UserLead


class UserLeadError(ValueError):
    pass


def save_user_lead(
    db: Session,
    *,
    user_id: int,
    lead_id: int,
    status: str = "new",
    notes: str | None = None,
    now: datetime | None = None,
) -> UserLead:
    now = now or datetime.utcnow()
    if db.get(User, user_id) is None:
        raise UserLeadError("Пользователь не найден")
    if db.get(Lead, lead_id) is None:
        raise UserLeadError("Лид не найден")

    row = db.scalar(
        select(UserLead).where(
            UserLead.user_id == user_id,
            UserLead.lead_id == lead_id,
        )
    )
    if row is None:
        row = UserLead(
            user_id=user_id,
            lead_id=lead_id,
            status=status,
            notes=notes,
            saved_at=now,
            last_seen_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.last_seen_at = now
        row.updated_at = now
        if status:
            row.status = status
        if notes is not None:
            row.notes = notes

    db.commit()
    db.refresh(row)
    return row


def update_user_lead(
    db: Session,
    *,
    user_id: int,
    lead_id: int,
    status: str | None = None,
    notes: str | None = None,
    now: datetime | None = None,
) -> UserLead:
    now = now or datetime.utcnow()
    row = db.scalar(
        select(UserLead).where(
            UserLead.user_id == user_id,
            UserLead.lead_id == lead_id,
        )
    )
    if row is None:
        raise UserLeadError("Лид не сохранён у этого пользователя")
    if status is not None:
        row.status = status
    if notes is not None:
        row.notes = notes
    row.updated_at = now
    db.commit()
    db.refresh(row)
    return row


def list_user_leads(
    db: Session,
    *,
    user_id: int,
    status: str | None = None,
    limit: int = 100,
) -> list[UserLead]:
    query = select(UserLead).where(UserLead.user_id == user_id)
    if status:
        query = query.where(UserLead.status == status)
    query = query.order_by(UserLead.updated_at.desc()).limit(max(1, min(limit, 500)))
    return list(db.scalars(query).all())
