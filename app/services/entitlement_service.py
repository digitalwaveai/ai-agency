from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import UserProject
from app.services.access_service import get_effective_access
from app.services.plan_service import get_project_limit
from app.services.subscription_service import get_active_subscription, subscription_plan


class EntitlementError(ValueError):
    pass


def project_limit_for_user(
    db: Session,
    *,
    user_id: int,
    now: datetime | None = None,
) -> int | None:
    access = get_effective_access(db, user_id, now=now)
    if access.unlimited:
        return None

    subscription = get_active_subscription(db, user_id, now=now)
    if subscription is None:
        raise EntitlementError("Нет активной подписки")
    plan = subscription_plan(db, subscription)
    limit = get_project_limit(plan.code)
    if limit is None:
        raise EntitlementError("Для тарифа не настроен лимит проектов")
    return limit


def count_user_projects(db: Session, *, user_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(UserProject.id)).where(UserProject.user_id == user_id)
        )
        or 0
    )


def ensure_project_capacity(
    db: Session,
    *,
    user_id: int,
    now: datetime | None = None,
) -> int | None:
    limit = project_limit_for_user(db, user_id=user_id, now=now)
    if limit is None:
        return None
    used = count_user_projects(db, user_id=user_id)
    if used >= limit:
        raise EntitlementError(
            f"Лимит проектов исчерпан: {used} из {limit}. "
            "Удалите ненужный проект или перейдите на более высокий тариф."
        )
    return limit
