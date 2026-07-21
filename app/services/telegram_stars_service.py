from __future__ import annotations

import html
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AdminNotification,
    Payment,
    PaymentEvent,
    Plan,
    Subscription,
    UserIdentity,
)
from app.services.payment_service import (
    PaymentError,
    create_pending_payment,
    process_successful_payment,
    record_payment_event,
)
from app.services.pricing_service import get_active_profile_price


TELEGRAM_STARS_PROVIDER = "telegram_stars"
TELEGRAM_STARS_CURRENCY = "XTR"
INVOICE_TTL_MINUTES = 30
PAID_PLAN_CODES = ("standard", "pro")
PAID_DURATION_MONTHS = (1, 3, 6, 12)
PLAN_NAMES = {
    "standard": "Стандарт",
    "pro": "Pro",
}


@dataclass(frozen=True)
class StarsInvoice:
    payment_id: int
    invoice_payload: str
    plan_code: str
    plan_name: str
    duration_months: int
    amount: int
    expires_at: datetime


@dataclass(frozen=True)
class CheckoutDecision:
    ok: bool
    error_message: str | None = None


@dataclass(frozen=True)
class StarsReceipt:
    payment: Payment
    plan: Plan
    subscription: Subscription
    duplicate: bool


def plan_name(plan_code: str) -> str:
    return PLAN_NAMES.get(plan_code, plan_code)


def duration_text(duration_months: int) -> str:
    if duration_months == 1:
        return "1 месяц"
    if duration_months in {3, 6}:
        return f"{duration_months} месяца"
    return f"{duration_months} месяцев"


def stars_price(
    db: Session,
    *,
    plan_code: str,
    duration_months: int,
) -> int:
    if plan_code not in PAID_PLAN_CODES:
        raise PaymentError("Неизвестный платный тариф")
    if duration_months not in PAID_DURATION_MONTHS:
        raise PaymentError("Срок должен быть 1, 3, 6 или 12 месяцев")
    row = get_active_profile_price(
        db,
        plan_code=plan_code,
        duration_months=duration_months,
        currency=TELEGRAM_STARS_CURRENCY,
    )
    if row is None or not row.is_active or row.amount_minor <= 0:
        raise PaymentError("Цена в Telegram Stars не настроена")
    return row.amount_minor


def list_stars_prices(db: Session, *, plan_code: str) -> dict[int, int]:
    return {
        duration: stars_price(
            db,
            plan_code=plan_code,
            duration_months=duration,
        )
        for duration in PAID_DURATION_MONTHS
    }


def format_stars_catalog(db: Session) -> str:
    plans = {
        plan.code: plan
        for plan in db.scalars(
            select(Plan).where(Plan.code.in_(PAID_PLAN_CODES))
        ).all()
    }
    missing = [code for code in PAID_PLAN_CODES if code not in plans]
    if missing:
        raise PaymentError("Тарифы ещё не настроены: " + ", ".join(missing))

    standard = plans["standard"]
    pro = plans["pro"]
    standard_from = stars_price(
        db,
        plan_code="standard",
        duration_months=1,
    )
    pro_from = stars_price(db, plan_code="pro", duration_months=1)
    return (
        "⭐ <b>Тарифы LeadPilot AI</b>\n\n"
        "<b>Стандарт</b> — для фрилансеров и небольших команд\n"
        f"• {standard.searches_limit} поисков и лидов в месяц\n"
        f"• {standard.audits_limit} анализов и "
        f"{standard.messages_limit} сообщений\n"
        f"• {standard.radars_limit} радара, до 3 проектов\n"
        f"• от <b>{standard_from} ⭐</b>\n\n"
        "<b>Pro</b> — для активного поиска и нескольких направлений\n"
        f"• {pro.searches_limit} поисков и лидов в месяц\n"
        f"• {pro.audits_limit} анализов и {pro.messages_limit} сообщений\n"
        f"• {pro.radars_limit} радаров, до 10 проектов\n"
        f"• от <b>{pro_from} ⭐</b>\n\n"
        "Оплата разовая. Автопродления нет. Выберите тариф:"
    )


def create_stars_invoice(
    db: Session,
    *,
    user_id: int,
    telegram_user_id: int,
    plan_code: str,
    duration_months: int,
    username: str | None = None,
    external_payment_id: str | None = None,
    now: datetime | None = None,
) -> StarsInvoice:
    now = now or datetime.utcnow()
    amount = stars_price(
        db,
        plan_code=plan_code,
        duration_months=duration_months,
    )
    expires_at = now + timedelta(minutes=INVOICE_TTL_MINUTES)
    invoice_payload = external_payment_id or (
        "xtr_" + secrets.token_urlsafe(18)
    )
    if not 1 <= len(invoice_payload.encode("utf-8")) <= 128:
        raise PaymentError("Некорректная длина идентификатора счёта")

    payment = create_pending_payment(
        db,
        user_id=user_id,
        plan_code=plan_code,
        duration_months=duration_months,
        provider=TELEGRAM_STARS_PROVIDER,
        external_payment_id=invoice_payload,
        amount_minor=amount,
        currency=TELEGRAM_STARS_CURRENCY,
        description=(
            f"LeadPilot AI — {plan_name(plan_code)}, "
            f"{duration_text(duration_months)}"
        ),
        metadata={
            "telegram_user_id": telegram_user_id,
            "telegram_username": username,
            "invoice_expires_at": expires_at.isoformat(),
            "one_time_payment": True,
        },
        now=now,
    )

    older = db.scalars(
        select(Payment).where(
            Payment.provider == TELEGRAM_STARS_PROVIDER,
            Payment.user_id == user_id,
            Payment.status == "pending",
            Payment.id != payment.id,
        )
    ).all()
    for item in older:
        item.status = "superseded"
        item.updated_at = now
    if older:
        db.commit()

    return StarsInvoice(
        payment_id=payment.id,
        invoice_payload=invoice_payload,
        plan_code=plan_code,
        plan_name=plan_name(plan_code),
        duration_months=duration_months,
        amount=amount,
        expires_at=expires_at,
    )


def _payment_for_payload(db: Session, invoice_payload: str) -> Payment | None:
    return db.scalar(
        select(Payment).where(
            Payment.provider == TELEGRAM_STARS_PROVIDER,
            Payment.external_payment_id == invoice_payload.strip(),
        )
    )


def validate_stars_checkout(
    db: Session,
    *,
    user_id: int,
    invoice_payload: str,
    currency: str,
    total_amount: int,
    now: datetime | None = None,
) -> CheckoutDecision:
    now = now or datetime.utcnow()
    payment = _payment_for_payload(db, invoice_payload)
    if payment is None:
        return CheckoutDecision(False, "Счёт не найден. Создайте новый в разделе тарифов.")
    if payment.user_id != user_id:
        return CheckoutDecision(False, "Этот счёт создан для другого пользователя.")
    if payment.status != "pending":
        return CheckoutDecision(False, "Счёт уже закрыт. Создайте новый в разделе тарифов.")
    if currency.strip().upper() != TELEGRAM_STARS_CURRENCY:
        return CheckoutDecision(False, "Неверная валюта счёта.")
    if total_amount != payment.amount_minor:
        return CheckoutDecision(False, "Сумма счёта изменилась. Создайте новый счёт.")
    if payment.created_at < now - timedelta(minutes=INVOICE_TTL_MINUTES):
        payment.status = "expired"
        payment.updated_at = now
        db.commit()
        return CheckoutDecision(False, "Срок счёта истёк. Создайте новый счёт.")
    return CheckoutDecision(True)


def fail_stars_invoice(
    db: Session,
    *,
    invoice_payload: str,
    error_message: str,
    now: datetime | None = None,
) -> None:
    now = now or datetime.utcnow()
    payment = _payment_for_payload(db, invoice_payload)
    if payment is None or payment.status != "pending":
        return
    payment.status = "failed"
    payment.updated_at = now
    db.commit()
    record_payment_event(
        db,
        provider=TELEGRAM_STARS_PROVIDER,
        provider_event_id=f"invoice_send_failed:{invoice_payload}",
        event_type="invoice_send_failed",
        payload={"error": error_message[:1000]},
        payment_id=payment.id,
        now=now,
    )


def process_stars_payment(
    db: Session,
    *,
    user_id: int,
    invoice_payload: str,
    currency: str,
    total_amount: int,
    telegram_payment_charge_id: str,
    provider_payment_charge_id: str | None = None,
    now: datetime | None = None,
) -> StarsReceipt:
    now = now or datetime.utcnow()
    payment = _payment_for_payload(db, invoice_payload)
    if payment is None:
        raise PaymentError("Оплаченный счёт не найден")
    if payment.user_id != user_id:
        raise PaymentError("Платёж принадлежит другому пользователю")
    if payment.status not in {"pending", "paid"}:
        raise PaymentError("Счёт не находится в состоянии оплаты")
    if currency.strip().upper() != TELEGRAM_STARS_CURRENCY:
        raise PaymentError("Telegram вернул неверную валюту платежа")
    if total_amount != payment.amount_minor:
        raise PaymentError("Telegram вернул неверную сумму платежа")
    charge_id = telegram_payment_charge_id.strip()
    if not charge_id:
        raise PaymentError("Telegram не вернул идентификатор платежа")

    existing_event = db.scalar(
        select(PaymentEvent).where(
            PaymentEvent.provider == TELEGRAM_STARS_PROVIDER,
            PaymentEvent.provider_event_id == charge_id,
        )
    )
    if existing_event is not None and existing_event.payment_id != payment.id:
        raise PaymentError("Идентификатор Telegram уже связан с другим платежом")
    duplicate = existing_event is not None and payment.status == "paid"

    processed, _ = process_successful_payment(
        db,
        provider=TELEGRAM_STARS_PROVIDER,
        external_payment_id=invoice_payload,
        provider_event_id=charge_id,
        payload={
            "invoice_payload": invoice_payload,
            "currency": TELEGRAM_STARS_CURRENCY,
            "total_amount": total_amount,
            "telegram_payment_charge_id": charge_id,
            "provider_payment_charge_id": provider_payment_charge_id,
        },
        now=now,
    )
    if processed.subscription_id is None:
        raise PaymentError("Подписка после оплаты не была создана")
    plan = db.get(Plan, processed.plan_id)
    subscription = db.get(Subscription, processed.subscription_id)
    if plan is None or subscription is None:
        raise PaymentError("Данные активированной подписки не найдены")
    return StarsReceipt(
        payment=processed,
        plan=plan,
        subscription=subscription,
        duplicate=duplicate,
    )


def format_owner_stars_report(db: Session, receipt: StarsReceipt) -> str:
    identity = db.scalar(
        select(UserIdentity).where(
            UserIdentity.user_id == receipt.payment.user_id,
            UserIdentity.platform == "telegram",
        )
    )
    username = f"@{identity.username}" if identity and identity.username else "—"
    telegram_id = identity.external_user_id if identity else "—"
    paid_at = receipt.payment.paid_at or datetime.utcnow()
    return (
        "💳 <b>Оплата Stars подтверждена</b>\n\n"
        f"Платёж: <code>{receipt.payment.id}</code>\n"
        f"Telegram ID: <code>{html.escape(str(telegram_id))}</code>\n"
        f"Username: <b>{html.escape(username)}</b>\n"
        f"Тариф: <b>{html.escape(receipt.plan.name)}</b>\n"
        f"Срок: <b>{duration_text(receipt.payment.duration_months)}</b>\n"
        f"Сумма: <b>{receipt.payment.amount_minor} ⭐</b>\n"
        f"Оплачено: <b>{paid_at:%d.%m.%Y %H:%M}</b>\n"
        f"Активно до: <b>{receipt.subscription.ends_at:%d.%m.%Y %H:%M}</b>"
    )


def admin_notification_needs_delivery(db: Session, *, payment_id: int) -> bool:
    notification = db.scalar(
        select(AdminNotification)
        .where(
            AdminNotification.payment_id == payment_id,
            AdminNotification.notification_type == "payment_paid",
        )
        .order_by(AdminNotification.id.desc())
    )
    return notification is not None and notification.status != "sent"


def mark_admin_notification_delivery(
    db: Session,
    *,
    payment_id: int,
    sent: bool,
    error_message: str | None = None,
    now: datetime | None = None,
) -> AdminNotification | None:
    now = now or datetime.utcnow()
    notification = db.scalar(
        select(AdminNotification)
        .where(
            AdminNotification.payment_id == payment_id,
            AdminNotification.notification_type == "payment_paid",
        )
        .order_by(AdminNotification.id.desc())
    )
    if notification is None:
        return None
    if notification.status == "sent":
        return notification
    notification.attempts += 1
    notification.updated_at = now
    if sent:
        notification.status = "sent"
        notification.sent_at = now
        notification.error_message = None
    else:
        notification.status = "pending"
        notification.available_at = now + timedelta(minutes=5)
        notification.error_message = (error_message or "Ошибка отправки")[:1000]
    db.commit()
    db.refresh(notification)
    return notification
