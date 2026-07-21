from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AdminNotification, Payment, PaymentEvent, Subscription
from app.services.access_service import ensure_owner_access
from app.services.payment_service import PaymentError
from app.services.plan_service import seed_default_plans
from app.services.pricing_service import set_active_pricing_profile
from app.services.subscription_service import register_identity
from app.services.telegram_stars_service import (
    create_stars_invoice,
    fail_stars_invoice,
    format_owner_stars_report,
    mark_admin_notification_delivery,
    process_stars_payment,
    validate_stars_checkout,
)


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture()
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    seed_default_plans(session)
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def make_user(db, external_id: str = "10001"):
    return register_identity(
        db,
        platform="telegram",
        external_user_id=external_id,
        username=f"user_{external_id}",
    )


def make_invoice(db, user, *, suffix: str = "one", now=None):
    return create_stars_invoice(
        db,
        user_id=user.id,
        telegram_user_id=int(user.id) + 10_000,
        username="buyer",
        plan_code="standard",
        duration_months=1,
        external_payment_id=f"xtr_test_{suffix}",
        now=now,
    )


def test_invoice_uses_active_xtr_price_and_snapshot(db):
    user = make_user(db)
    invoice = make_invoice(db, user)

    payment = db.get(Payment, invoice.payment_id)
    assert invoice.amount == 970
    assert invoice.invoice_payload == "xtr_test_one"
    assert payment.provider == "telegram_stars"
    assert payment.currency == "XTR"
    assert payment.amount_minor == 970
    assert payment.status == "pending"
    assert '"profile": "production"' in payment.metadata_json
    assert '"one_time_payment": true' in payment.metadata_json


def test_invoice_uses_one_star_in_test_profile(db):
    owner = make_user(db, "99999")
    ensure_owner_access(db, user_id=owner.id)
    set_active_pricing_profile(
        db,
        owner_user_id=owner.id,
        profile_code="test",
    )
    user = make_user(db)

    invoice = make_invoice(db, user)
    assert invoice.amount == 1
    assert db.get(Payment, invoice.payment_id).amount_minor == 1


def test_new_invoice_supersedes_older_pending_invoice(db):
    user = make_user(db)
    first = make_invoice(db, user, suffix="first")
    second = make_invoice(db, user, suffix="second")

    assert db.get(Payment, first.payment_id).status == "superseded"
    assert db.get(Payment, second.payment_id).status == "pending"


def test_precheckout_validates_owner_currency_amount_and_age(db):
    start = datetime(2026, 7, 21, 12, 0, 0)
    user = make_user(db)
    other = make_user(db, "10002")
    invoice = make_invoice(db, user, now=start)

    ok = validate_stars_checkout(
        db,
        user_id=user.id,
        invoice_payload=invoice.invoice_payload,
        currency="XTR",
        total_amount=970,
        now=start + timedelta(minutes=2),
    )
    assert ok.ok is True

    wrong_user = validate_stars_checkout(
        db,
        user_id=other.id,
        invoice_payload=invoice.invoice_payload,
        currency="XTR",
        total_amount=970,
        now=start + timedelta(minutes=2),
    )
    assert wrong_user.ok is False
    assert "другого пользователя" in wrong_user.error_message

    wrong_amount = validate_stars_checkout(
        db,
        user_id=user.id,
        invoice_payload=invoice.invoice_payload,
        currency="XTR",
        total_amount=971,
        now=start + timedelta(minutes=2),
    )
    assert wrong_amount.ok is False
    assert "Сумма" in wrong_amount.error_message

    expired = validate_stars_checkout(
        db,
        user_id=user.id,
        invoice_payload=invoice.invoice_payload,
        currency="XTR",
        total_amount=970,
        now=start + timedelta(minutes=31),
    )
    assert expired.ok is False
    assert db.get(Payment, invoice.payment_id).status == "expired"


def test_successful_stars_payment_activates_once_and_is_idempotent(db):
    start = datetime(2026, 7, 21, 12, 0, 0)
    user = make_user(db)
    invoice = make_invoice(db, user, now=start)

    first = process_stars_payment(
        db,
        user_id=user.id,
        invoice_payload=invoice.invoice_payload,
        currency="XTR",
        total_amount=970,
        telegram_payment_charge_id="telegram_charge_1",
        provider_payment_charge_id="",
        now=start + timedelta(minutes=1),
    )
    second = process_stars_payment(
        db,
        user_id=user.id,
        invoice_payload=invoice.invoice_payload,
        currency="XTR",
        total_amount=970,
        telegram_payment_charge_id="telegram_charge_1",
        provider_payment_charge_id="",
        now=start + timedelta(minutes=2),
    )

    assert first.duplicate is False
    assert second.duplicate is True
    assert first.payment.status == "paid"
    assert first.subscription.status == "active"
    assert first.subscription.ends_at == start + timedelta(minutes=1, days=30)
    assert db.scalar(select(func.count(Subscription.id))) == 1
    assert db.scalar(select(func.count(PaymentEvent.id))) == 1
    assert db.scalar(select(func.count(AdminNotification.id))) == 1


def test_success_event_cannot_be_reused_for_another_invoice(db):
    first_user = make_user(db, "10001")
    first_invoice = make_invoice(db, first_user, suffix="first")
    process_stars_payment(
        db,
        user_id=first_user.id,
        invoice_payload=first_invoice.invoice_payload,
        currency="XTR",
        total_amount=970,
        telegram_payment_charge_id="same_charge",
    )

    second_user = make_user(db, "10002")
    second_invoice = make_invoice(db, second_user, suffix="second")
    with pytest.raises(PaymentError, match="другим платежом"):
        process_stars_payment(
            db,
            user_id=second_user.id,
            invoice_payload=second_invoice.invoice_payload,
            currency="XTR",
            total_amount=970,
            telegram_payment_charge_id="same_charge",
        )


def test_failed_invoice_is_closed_before_checkout(db):
    user = make_user(db)
    invoice = make_invoice(db, user)
    fail_stars_invoice(
        db,
        invoice_payload=invoice.invoice_payload,
        error_message="Telegram unavailable",
    )

    assert db.get(Payment, invoice.payment_id).status == "failed"
    decision = validate_stars_checkout(
        db,
        user_id=user.id,
        invoice_payload=invoice.invoice_payload,
        currency="XTR",
        total_amount=970,
    )
    assert decision.ok is False


def test_owner_report_and_delivery_state(db):
    user = register_identity(
        db,
        platform="telegram",
        external_user_id="10001",
        username="buyer<script>",
    )
    invoice = make_invoice(db, user)
    receipt = process_stars_payment(
        db,
        user_id=user.id,
        invoice_payload=invoice.invoice_payload,
        currency="XTR",
        total_amount=970,
        telegram_payment_charge_id="report_charge",
    )

    report = format_owner_stars_report(db, receipt)
    assert "Оплата Stars подтверждена" in report
    assert "buyer&lt;script&gt;" in report
    assert "970 ⭐" in report

    notification = mark_admin_notification_delivery(
        db,
        payment_id=receipt.payment.id,
        sent=True,
    )
    assert notification.status == "sent"
    assert notification.attempts == 1
    assert notification.sent_at is not None
