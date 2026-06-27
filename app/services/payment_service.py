from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ActivationToken,
    AdminNotification,
    Payment,
    PaymentEvent,
    Plan,
    User,
)
from app.services.plan_service import get_plan_by_code, get_plan_price
from app.services.subscription_service import activate_subscription


class PaymentError(ValueError):
    pass


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _normalize(value: str) -> str:
    return value.strip().lower()


def create_pending_payment(
    db: Session,
    *,
    user_id: int | None,
    plan_code: str,
    duration_months: int,
    provider: str,
    external_payment_id: str,
    amount_minor: int,
    currency: str = "RUB",
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
    validate_rub_amount: bool = True,
    now: datetime | None = None,
) -> Payment:
    now = now or datetime.utcnow()
    provider = _normalize(provider)
    external_payment_id = external_payment_id.strip()
    currency = currency.strip().upper()

    if not provider or not external_payment_id:
        raise PaymentError("provider и external_payment_id обязательны")
    if amount_minor < 0:
        raise PaymentError("Сумма платежа не может быть отрицательной")
    if user_id is not None and db.get(User, user_id) is None:
        raise PaymentError("Пользователь не найден")

    existing = db.scalar(
        select(Payment).where(
            Payment.provider == provider,
            Payment.external_payment_id == external_payment_id,
        )
    )
    if existing is not None:
        if (
            existing.user_id != user_id
            or existing.duration_months != duration_months
            or existing.amount_minor != amount_minor
            or existing.currency != currency
        ):
            raise PaymentError("Внешний ID уже используется другим платежом")
        return existing

    plan = get_plan_by_code(db, plan_code)
    if plan is None or not plan.is_active or plan.code == "demo":
        raise PaymentError("Платный тариф не найден или отключён")
    price = get_plan_price(db, plan, duration_months)
    if price is None:
        raise PaymentError("Для тарифа нет такой длительности")

    expected_minor = price.price_rub * 100
    if validate_rub_amount and currency == "RUB" and amount_minor != expected_minor:
        raise PaymentError(
            f"Неверная сумма: ожидалось {expected_minor} копеек"
        )

    payment = Payment(
        user_id=user_id,
        plan_id=plan.id,
        plan_price_id=price.id,
        provider=provider,
        external_payment_id=external_payment_id,
        status="pending",
        amount_minor=amount_minor,
        currency=currency,
        duration_months=duration_months,
        description=description,
        metadata_json=_json_dumps(metadata),
        created_at=now,
        updated_at=now,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


def record_payment_event(
    db: Session,
    *,
    provider: str,
    provider_event_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    payment_id: int | None = None,
    now: datetime | None = None,
) -> tuple[PaymentEvent, bool]:
    now = now or datetime.utcnow()
    provider = _normalize(provider)
    provider_event_id = provider_event_id.strip()
    event_type = event_type.strip().lower()

    existing = db.scalar(
        select(PaymentEvent).where(
            PaymentEvent.provider == provider,
            PaymentEvent.provider_event_id == provider_event_id,
        )
    )
    if existing is not None:
        return existing, False

    event = PaymentEvent(
        payment_id=payment_id,
        provider=provider,
        provider_event_id=provider_event_id,
        event_type=event_type,
        status="received",
        payload_json=_json_dumps(payload),
        received_at=now,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event, True


def _queue_payment_notification(
    db: Session,
    *,
    payment: Payment,
    notification_type: str,
    payload: dict[str, Any],
    now: datetime,
) -> AdminNotification:
    key = f"{notification_type}:{payment.provider}:{payment.external_payment_id}"
    existing = db.scalar(
        select(AdminNotification).where(
            AdminNotification.idempotency_key == key
        )
    )
    if existing is not None:
        return existing

    notification = AdminNotification(
        user_id=payment.user_id,
        payment_id=payment.id,
        notification_type=notification_type,
        status="pending",
        idempotency_key=key,
        payload_json=_json_dumps(payload),
        available_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


def process_successful_payment(
    db: Session,
    *,
    provider: str,
    external_payment_id: str,
    provider_event_id: str,
    payload: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> tuple[Payment, PaymentEvent]:
    now = now or datetime.utcnow()
    provider = _normalize(provider)
    payment = db.scalar(
        select(Payment).where(
            Payment.provider == provider,
            Payment.external_payment_id == external_payment_id.strip(),
        )
    )
    if payment is None:
        raise PaymentError("Платёж не найден")

    event, _ = record_payment_event(
        db,
        provider=provider,
        provider_event_id=provider_event_id,
        event_type="payment_succeeded",
        payload=payload,
        payment_id=payment.id,
        now=now,
    )

    if payment.status == "paid" and payment.subscription_id is not None:
        event.payment_id = payment.id
        event.status = "processed"
        event.processed_at = event.processed_at or now
        event.error_message = None
        db.commit()
        return payment, event

    payment.status = "paid"
    payment.paid_at = payment.paid_at or now
    payment.updated_at = now
    db.commit()

    try:
        if payment.user_id is not None and payment.subscription_id is None:
            plan = db.get(Plan, payment.plan_id)
            if plan is None:
                raise PaymentError("Тариф платежа не найден")
            subscription = activate_subscription(
                db,
                user_id=payment.user_id,
                plan_code=plan.code,
                duration_months=payment.duration_months,
                source=f"payment:{payment.provider}",
                now=now,
            )
            payment.subscription_id = subscription.id

        event.payment_id = payment.id
        event.status = "processed"
        event.processed_at = now
        event.error_message = None
        db.commit()
        db.refresh(payment)

        notification_type = (
            "payment_paid"
            if payment.subscription_id is not None
            else "payment_paid_unlinked"
        )
        _queue_payment_notification(
            db,
            payment=payment,
            notification_type=notification_type,
            payload={
                "payment_id": payment.id,
                "user_id": payment.user_id,
                "amount_minor": payment.amount_minor,
                "currency": payment.currency,
                "subscription_id": payment.subscription_id,
            },
            now=now,
        )
        return payment, event
    except Exception as exc:
        event.status = "error"
        event.error_message = str(exc)[:1000]
        db.commit()
        _queue_payment_notification(
            db,
            payment=payment,
            notification_type="payment_activation_error",
            payload={"payment_id": payment.id, "error": str(exc)[:1000]},
            now=now,
        )
        raise


def create_activation_token(
    db: Session,
    *,
    payment_id: int,
    ttl_hours: int = 72,
    now: datetime | None = None,
) -> str:
    now = now or datetime.utcnow()
    if not 1 <= ttl_hours <= 24 * 30:
        raise PaymentError("Срок токена должен быть от 1 до 720 часов")

    payment = db.get(Payment, payment_id)
    if payment is None:
        raise PaymentError("Платёж не найден")
    if payment.status != "paid":
        raise PaymentError("Токен можно создать только для оплаченного платежа")

    active_tokens = db.scalars(
        select(ActivationToken).where(
            ActivationToken.payment_id == payment_id,
            ActivationToken.status == "active",
        )
    ).all()
    for item in active_tokens:
        item.status = "superseded"

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = ActivationToken(
        payment_id=payment_id,
        token_hash=token_hash,
        status="active",
        expires_at=now + timedelta(hours=ttl_hours),
        created_at=now,
    )
    db.add(row)
    db.commit()
    return token


def consume_activation_token(
    db: Session,
    *,
    token: str,
    user_id: int,
    now: datetime | None = None,
) -> Payment:
    now = now or datetime.utcnow()
    if db.get(User, user_id) is None:
        raise PaymentError("Пользователь не найден")

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = db.scalar(
        select(ActivationToken).where(
            ActivationToken.token_hash == token_hash
        )
    )
    if row is None or row.status != "active":
        raise PaymentError("Токен недействителен или уже использован")
    if row.expires_at <= now:
        row.status = "expired"
        db.commit()
        raise PaymentError("Срок действия токена истёк")

    payment = db.get(Payment, row.payment_id)
    if payment is None or payment.status != "paid":
        raise PaymentError("Оплаченный платёж не найден")
    if payment.user_id is not None and payment.user_id != user_id:
        raise PaymentError("Платёж уже привязан к другому пользователю")

    payment.user_id = user_id
    if payment.subscription_id is None:
        plan = db.get(Plan, payment.plan_id)
        if plan is None:
            raise PaymentError("Тариф платежа не найден")
        db.commit()
        subscription = activate_subscription(
            db,
            user_id=user_id,
            plan_code=plan.code,
            duration_months=payment.duration_months,
            source=f"activation:{payment.provider}",
            now=now,
        )
        payment.subscription_id = subscription.id

    row.status = "used"
    row.used_at = now
    row.used_by_user_id = user_id
    db.commit()
    db.refresh(payment)
    return payment
