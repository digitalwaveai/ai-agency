from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Plan, Subscription, User, UserIdentity
from app.services.plan_service import get_plan_by_code, get_plan_price


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

    expired = db.scalars(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status == "active",
            Subscription.ends_at <= now,
        )
    ).all()
    for subscription in expired:
        subscription.status = "expired"

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
    if expired:
        db.commit()
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

    plan = get_plan_by_code(db, plan_code)
    if plan is None or not plan.is_active:
        raise SubscriptionError("Тариф не найден или отключён")
    if plan.code == "demo":
        raise SubscriptionError("Для Demo используйте activate_demo_subscription")
    if get_plan_price(db, plan, duration_months) is None:
        raise SubscriptionError("Для тарифа нет такой длительности")

    current = get_active_subscription(db, user_id, now=now)
    starts_at = current.ends_at if current and current.ends_at > now else now
    ends_at = starts_at + timedelta(days=30 * duration_months)
    status = "active" if starts_at <= now else "scheduled"

    subscription = Subscription(
        user_id=user_id,
        plan_id=plan.id,
        status=status,
        duration_months=duration_months,
        source=source,
        starts_at=starts_at,
        ends_at=ends_at,
        next_usage_reset_at=min(starts_at + timedelta(days=30), ends_at),
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
    now = now or datetime.utcnow()
    user = db.get(User, user_id)
    if user is None:
        raise SubscriptionError("Пользователь не найден")

    demo = get_plan_by_code(db, "demo")
    if demo is None:
        raise SubscriptionError("Тариф Demo не создан")

    already_used = db.scalar(
        select(Subscription.id).where(
            Subscription.user_id == user_id,
            Subscription.plan_id == demo.id,
        )
    )
    if already_used is not None:
        raise SubscriptionError("Demo уже использован")

    subscription = Subscription(
        user_id=user_id,
        plan_id=demo.id,
        status="active",
        duration_months=1,
        source="demo",
        starts_at=now,
        ends_at=now + timedelta(days=30),
        next_usage_reset_at=now + timedelta(days=30),
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return subscription


def subscription_plan(db: Session, subscription: Subscription) -> Plan:
    plan = db.get(Plan, subscription.plan_id)
    if plan is None:
        raise SubscriptionError("Тариф подписки не найден")
    return plan
