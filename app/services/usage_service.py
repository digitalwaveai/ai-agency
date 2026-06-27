from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import case, select, update
from sqlalchemy.orm import Session

from app.models import Plan, Subscription, UsageEvent, UsagePeriod
from app.services.access_service import get_effective_access
from app.services.subscription_service import get_active_subscription


RESOURCE_FIELDS: dict[str, tuple[str, str]] = {
    "searches": ("searches_used", "searches_limit"),
    "saved_leads": ("saved_leads_used", "saved_leads_limit"),
    "audits": ("audits_used", "audits_limit"),
    "messages": ("messages_used", "messages_limit"),
}


class UsageError(ValueError):
    pass


class UsageLimitExceeded(UsageError):
    def __init__(self, resource: str, used: int, limit: int):
        self.resource = resource
        self.used = used
        self.limit = limit
        super().__init__(f"Лимит {resource} исчерпан: {used} из {limit}")


@dataclass(frozen=True)
class UsageReservation:
    event_id: int | None
    usage_period_id: int | None
    resource: str
    amount: int
    already_processed: bool = False
    bypassed: bool = False
    access_role: str | None = None


def _require_active_subscription(
    db: Session,
    subscription: Subscription,
    now: datetime,
) -> Plan:
    if subscription.status != "active":
        raise UsageError("Подписка не активна")
    if subscription.starts_at > now or subscription.ends_at <= now:
        raise UsageError("Срок подписки не активен")
    plan = db.get(Plan, subscription.plan_id)
    if plan is None or not plan.is_active:
        raise UsageError("Тариф подписки недоступен")
    return plan


def _period_bounds(
    subscription: Subscription,
    now: datetime,
) -> tuple[datetime, datetime]:
    if now < subscription.starts_at or now >= subscription.ends_at:
        raise UsageError("Дата вне срока подписки")

    elapsed = now - subscription.starts_at
    period_number = elapsed.days // 30
    period_start = subscription.starts_at + timedelta(days=30 * period_number)
    period_end = min(period_start + timedelta(days=30), subscription.ends_at)
    return period_start, period_end


def get_or_create_usage_period(
    db: Session,
    subscription: Subscription,
    *,
    now: datetime | None = None,
) -> UsagePeriod:
    now = now or datetime.utcnow()
    _require_active_subscription(db, subscription, now)
    period_start, period_end = _period_bounds(subscription, now)

    period = db.scalar(
        select(UsagePeriod).where(
            UsagePeriod.subscription_id == subscription.id,
            UsagePeriod.period_start == period_start,
        )
    )
    if period is not None:
        return period

    period = UsagePeriod(
        user_id=subscription.user_id,
        subscription_id=subscription.id,
        period_start=period_start,
        period_end=period_end,
    )
    db.add(period)
    db.flush()
    return period


def usage_snapshot(
    db: Session,
    subscription: Subscription,
    *,
    now: datetime | None = None,
) -> dict[str, dict[str, int]]:
    now = now or datetime.utcnow()
    plan = _require_active_subscription(db, subscription, now)
    period = get_or_create_usage_period(db, subscription, now=now)
    snapshot: dict[str, dict[str, int]] = {}

    for resource, (used_field, limit_field) in RESOURCE_FIELDS.items():
        used = int(getattr(period, used_field) or 0)
        limit = int(getattr(plan, limit_field) or 0)
        snapshot[resource] = {
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
        }
    return snapshot


def reserve_usage(
    db: Session,
    subscription: Subscription,
    *,
    resource: str,
    amount: int = 1,
    reason: str | None = None,
    idempotency_key: str | None = None,
    reservation_minutes: int = 15,
    now: datetime | None = None,
) -> UsageReservation:
    now = now or datetime.utcnow()
    if amount <= 0:
        raise UsageError("amount должен быть больше нуля")
    if resource not in RESOURCE_FIELDS:
        raise UsageError(f"Неизвестный ресурс: {resource}")

    if idempotency_key:
        existing = db.scalar(
            select(UsageEvent).where(
                UsageEvent.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return UsageReservation(
                event_id=existing.id,
                usage_period_id=existing.usage_period_id,
                resource=existing.resource,
                amount=abs(existing.delta),
                already_processed=True,
            )

    plan = _require_active_subscription(db, subscription, now)
    period = get_or_create_usage_period(db, subscription, now=now)
    used_field, limit_field = RESOURCE_FIELDS[resource]
    used_column = getattr(UsagePeriod, used_field)
    limit = int(getattr(plan, limit_field) or 0)

    result = db.execute(
        update(UsagePeriod)
        .where(
            UsagePeriod.id == period.id,
            used_column + amount <= limit,
        )
        .values({used_field: used_column + amount})
    )

    if result.rowcount != 1:
        db.rollback()
        fresh = db.get(UsagePeriod, period.id)
        used = int(getattr(fresh, used_field) or 0) if fresh else limit
        raise UsageLimitExceeded(resource, used, limit)

    event = UsageEvent(
        user_id=subscription.user_id,
        subscription_id=subscription.id,
        usage_period_id=period.id,
        event_type="reserve",
        resource=resource,
        delta=amount,
        status="reserved",
        reason=reason,
        idempotency_key=idempotency_key,
        reserved_until=now + timedelta(minutes=reservation_minutes),
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    return UsageReservation(
        event_id=event.id,
        usage_period_id=period.id,
        resource=resource,
        amount=amount,
    )



def reserve_user_usage(
    db: Session,
    *,
    user_id: int,
    resource: str,
    amount: int = 1,
    reason: str | None = None,
    idempotency_key: str | None = None,
    reservation_minutes: int = 15,
    now: datetime | None = None,
) -> UsageReservation:
    now = now or datetime.utcnow()
    if amount <= 0:
        raise UsageError("amount должен быть больше нуля")
    if resource not in RESOURCE_FIELDS:
        raise UsageError(f"Неизвестный ресурс: {resource}")

    access = get_effective_access(db, user_id, now=now)
    if access.unlimited:
        return UsageReservation(
            event_id=None,
            usage_period_id=None,
            resource=resource,
            amount=amount,
            bypassed=True,
            access_role=access.role,
        )

    subscription = get_active_subscription(db, user_id, now=now)
    if subscription is None:
        raise UsageError("Нет активной подписки")
    return reserve_usage(
        db,
        subscription,
        resource=resource,
        amount=amount,
        reason=reason,
        idempotency_key=idempotency_key,
        reservation_minutes=reservation_minutes,
        now=now,
    )

def confirm_usage(
    db: Session,
    reservation: UsageReservation,
    *,
    now: datetime | None = None,
) -> UsageEvent | None:
    now = now or datetime.utcnow()
    if reservation.bypassed:
        return None
    event = db.get(UsageEvent, reservation.event_id)
    if event is None:
        raise UsageError("Резервирование не найдено")
    if event.status == "consumed":
        return event
    if event.status != "reserved":
        raise UsageError(f"Нельзя подтвердить событие со статусом {event.status}")

    event.status = "consumed"
    event.event_type = "consume"
    event.reserved_until = None
    event.updated_at = now
    db.commit()
    db.refresh(event)
    return event


def release_usage(
    db: Session,
    reservation: UsageReservation,
    *,
    reason: str | None = None,
    now: datetime | None = None,
) -> UsageEvent | None:
    now = now or datetime.utcnow()
    if reservation.bypassed:
        return None
    event = db.get(UsageEvent, reservation.event_id)
    if event is None:
        raise UsageError("Резервирование не найдено")
    if event.status == "released":
        return event
    if event.status != "reserved":
        raise UsageError(f"Нельзя вернуть событие со статусом {event.status}")

    used_field, _ = RESOURCE_FIELDS[event.resource]
    used_column = getattr(UsagePeriod, used_field)
    db.execute(
        update(UsagePeriod)
        .where(UsagePeriod.id == event.usage_period_id)
        .values(
            {
                used_field: case(
                    (used_column >= abs(event.delta), used_column - abs(event.delta)),
                    else_=0,
                )
            }
        )
    )
    event.status = "released"
    event.reserved_until = None
    event.updated_at = now

    reversal = UsageEvent(
        user_id=event.user_id,
        subscription_id=event.subscription_id,
        usage_period_id=event.usage_period_id,
        event_type="release",
        resource=event.resource,
        delta=-abs(event.delta),
        status="completed",
        reason=reason or "Технический возврат лимита",
    )
    db.add(reversal)
    db.commit()
    db.refresh(event)
    return event


def release_expired_reservations(
    db: Session,
    *,
    now: datetime | None = None,
) -> int:
    now = now or datetime.utcnow()
    expired = db.scalars(
        select(UsageEvent).where(
            UsageEvent.status == "reserved",
            UsageEvent.reserved_until.is_not(None),
            UsageEvent.reserved_until <= now,
        )
    ).all()

    released = 0
    for event in expired:
        release_usage(
            db,
            UsageReservation(
                event_id=event.id,
                usage_period_id=event.usage_period_id,
                resource=event.resource,
                amount=abs(event.delta),
            ),
            reason="Автоматический возврат просроченного резерва",
            now=now,
        )
        released += 1
    return released
