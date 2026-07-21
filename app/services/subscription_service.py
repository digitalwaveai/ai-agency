from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Plan, Subscription, User, UserIdentity
from app.services.plan_service import (
    get_plan_by_code,
    get_plan_price,
    normalize_purchase_plan_code,
)


TRIAL_DAYS = 7
BILLING_PERIOD_DAYS = 30
TRIAL_PLAN_CODES = {"trial", "demo"}


class SubscriptionError(ValueError):
    pass


def register_identity(
    db: Session,
    *,
    platform: str,
    external_user_id: str | int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    display_name: str | None = None,
    now: datetime | None = None,
) -> User:
    now = now or datetime.utcnow()
    platform = platform.strip().lower()
    external_id = str(external_user_id).strip()

    if not platform or not external_id:
        raise ValueError("platform и external_user_id обязательны")

    identity = db.scalar(
        select(UserIdentity).where(
            UserIdentity.platform == platform,
            UserIdentity.external_user_id == external_id,
        )
    )

    if identity is not None:
        identity.username = username
        identity.first_name = first_name
        identity.last_name = last_name
        identity.last_seen_at = now
        user = db.get(User, identity.user_id)
        if user is None:
            raise RuntimeError("UserIdentity ссылается на отсутствующего пользователя")
        if display_name:
            user.display_name = display_name
        db.commit()
        db.refresh(user)
        return user

    effective_name = display_name or " ".join(
        part for part in (first_name, last_name) if part
    ).strip() or username

    user = User(
        public_id=str(uuid4()),
        display_name=effective_name,
    )
    db.add(user)
    db.flush()

    db.add(
        UserIdentity(
            user_id=user.id,
            platform=platform,
            external_user_id=external_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            created_at=now,
            last_seen_at=now,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def get_active_subscription(
    db: Session,
    user_id: int,
    *,
    now: datetime | None = None,
) -> Subscription | None:
    now = now or datetime.utcnow()
    changed = False

    expired = db.scalars(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status == "active",
            Subscription.ends_at <= now,
        )
    ).all()
    for subscription in expired:
        subscription.status = "expired"
        changed = True

    active = db.scalar(
        select(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.status == "active",
            Subscription.starts_at <= now,
            Subscription.ends_at > now,
        )
        .order_by(Subscription.ends_at.desc())
    )

    if active is None:
        scheduled = db.scalar(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == "scheduled",
                Subscription.starts_at <= now,
                Subscription.ends_at > now,
            )
            .order_by(Subscription.starts_at.asc())
        )
        if scheduled is not None:
            scheduled.status = "active"
            active = scheduled
            changed = True

    if changed:
        db.commit()
        if active is not None:
            db.refresh(active)
    return active


def activate_subscription(
    db: Session,
    *,
    user_id: int,
    plan_code: str,
    duration_months: int,
    source: str = "manual",
    now: datetime | None = None,
) -> Subscription:
    now = now or datetime.utcnow()
    user = db.get(User, user_id)
    if user is None:
        raise SubscriptionError("Пользователь не найден")

    normalized_code = normalize_purchase_plan_code(plan_code)
    plan = get_plan_by_code(db, normalized_code)
    if plan is None or not plan.is_active:
        raise SubscriptionError("Тариф не найден или отключён")
    if plan.code in TRIAL_PLAN_CODES:
        raise SubscriptionError("Для пробного тарифа используйте activate_trial_subscription")
    if get_plan_price(db, plan, duration_months) is None:
        raise SubscriptionError("Для тарифа нет такой длительности")

    current = get_active_subscription(db, user_id, now=now)
    starts_at = current.ends_at if current and current.ends_at > now else now
    ends_at = starts_at + timedelta(days=BILLING_PERIOD_DAYS * duration_months)
    status = "active" if starts_at <= now else "scheduled"

    subscription = Subscription(
        user_id=user_id,
        plan_id=plan.id,
        status=status,
        duration_months=duration_months,
        source=source,
        starts_at=starts_at,
        ends_at=ends_at,
        next_usage_reset_at=min(
            starts_at + timedelta(days=BILLING_PERIOD_DAYS),
            ends_at,
        ),
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return subscription


def activate_trial_subscription(
    db: Session,
    *,
    user_id: int,
    now: datetime | None = None,
) -> Subscription:
    now = now or datetime.utcnow()
    user = db.get(User, user_id)
    if user is None:
        raise SubscriptionError("Пользователь не найден")

    trial = get_plan_by_code(db, "trial")
    if trial is None or not trial.is_active:
        raise SubscriptionError("Пробный тариф не создан")

    trial_plan_ids = db.scalars(
        select(Plan.id).where(Plan.code.in_(TRIAL_PLAN_CODES))
    ).all()
    already_used = db.scalar(
        select(Subscription.id).where(
            Subscription.user_id == user_id,
            Subscription.plan_id.in_(trial_plan_ids),
        )
    )
    if already_used is not None:
        raise SubscriptionError("Пробный период уже использован")

    ends_at = now + timedelta(days=TRIAL_DAYS)
    subscription = Subscription(
        user_id=user_id,
        plan_id=trial.id,
        status="active",
        duration_months=1,
        source="trial",
        starts_at=now,
        ends_at=ends_at,
        next_usage_reset_at=ends_at,
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return subscription


def activate_demo_subscription(
    db: Session,
    *,
    user_id: int,
    now: datetime | None = None,
) -> Subscription:
    """Backward-compatible alias for old Telegram registration code."""

    return activate_trial_subscription(db, user_id=user_id, now=now)


def subscription_plan(db: Session, subscription: Subscription) -> Plan:
    plan = db.get(Plan, subscription.plan_id)
    if plan is None:
        raise SubscriptionError("Тариф подписки не найден")
    return plan
