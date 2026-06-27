from datetime import datetime, timedelta
import json

import pytest
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    ActivationToken,
    AdminNotification,
    AnalyticsEvent,
    Lead,
    Payment,
    PaymentEvent,
    SearchRun,
    Subscription,
    UserLead,
)
from app.services.analytics_service import (
    finish_search_run,
    log_analytics_event,
    queue_admin_notification,
    start_search_run,
)
from app.services.payment_service import (
    PaymentError,
    consume_activation_token,
    create_activation_token,
    create_pending_payment,
    process_successful_payment,
    record_payment_event,
)
from app.services.plan_service import seed_default_plans
from app.services.subscription_service import register_identity
from app.services.user_lead_service import (
    list_user_leads,
    save_user_lead,
    update_user_lead,
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


def make_user(db, external_id: str):
    return register_identity(
        db,
        platform="telegram",
        external_user_id=external_id,
        username=f"user_{external_id}",
    )


def make_lead(db, suffix: str = "1"):
    lead = Lead(
        name=f"Салон {suffix}",
        niche="косметолог",
        city="Москва",
        country="Россия",
        source_url=f"https://example.com/{suffix}",
        status="new",
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def test_part3_tables_registered(db):
    expected = {
        "payments",
        "payment_events",
        "activation_tokens",
        "user_leads",
        "analytics_events",
        "search_runs",
        "admin_notifications",
    }
    assert expected.issubset(set(inspect(engine).get_table_names()))


def test_pending_payment_is_idempotent(db):
    user = make_user(db, "p1")
    first = create_pending_payment(
        db,
        user_id=user.id,
        plan_code="solo",
        duration_months=1,
        provider="website",
        external_payment_id="pay-1",
        amount_minor=249000,
    )
    second = create_pending_payment(
        db,
        user_id=user.id,
        plan_code="solo",
        duration_months=1,
        provider="website",
        external_payment_id="pay-1",
        amount_minor=249000,
    )
    assert first.id == second.id
    assert db.scalar(select(func.count(Payment.id))) == 1


def test_duplicate_payment_event_is_not_created_twice(db):
    first, created_first = record_payment_event(
        db,
        provider="website",
        provider_event_id="evt-1",
        event_type="payment_succeeded",
        payload={"ok": True},
    )
    second, created_second = record_payment_event(
        db,
        provider="website",
        provider_event_id="evt-1",
        event_type="payment_succeeded",
        payload={"ok": True},
    )
    assert first.id == second.id
    assert created_first is True
    assert created_second is False
    assert db.scalar(select(func.count(PaymentEvent.id))) == 1


def test_successful_payment_activates_subscription(db):
    user = make_user(db, "p2")
    payment = create_pending_payment(
        db,
        user_id=user.id,
        plan_code="pro",
        duration_months=3,
        provider="website",
        external_payment_id="pay-2",
        amount_minor=1709000,
    )
    processed, event = process_successful_payment(
        db,
        provider="website",
        external_payment_id="pay-2",
        provider_event_id="evt-2",
    )
    assert processed.id == payment.id
    assert processed.status == "paid"
    assert processed.subscription_id is not None
    assert event.status == "processed"
    subscription = db.get(Subscription, processed.subscription_id)
    assert subscription.user_id == user.id
    assert subscription.duration_months == 3


def test_repeated_success_does_not_duplicate_subscription(db):
    user = make_user(db, "p3")
    create_pending_payment(
        db,
        user_id=user.id,
        plan_code="solo",
        duration_months=1,
        provider="website",
        external_payment_id="pay-3",
        amount_minor=249000,
    )
    first, _ = process_successful_payment(
        db,
        provider="website",
        external_payment_id="pay-3",
        provider_event_id="evt-3a",
    )
    second, _ = process_successful_payment(
        db,
        provider="website",
        external_payment_id="pay-3",
        provider_event_id="evt-3b",
    )
    assert first.subscription_id == second.subscription_id
    assert db.scalar(select(func.count(Subscription.id))) == 1


def test_unlinked_paid_payment_waits_for_activation(db):
    payment = create_pending_payment(
        db,
        user_id=None,
        plan_code="solo",
        duration_months=1,
        provider="website",
        external_payment_id="pay-4",
        amount_minor=249000,
    )
    processed, _ = process_successful_payment(
        db,
        provider="website",
        external_payment_id="pay-4",
        provider_event_id="evt-4",
    )
    assert processed.id == payment.id
    assert processed.status == "paid"
    assert processed.user_id is None
    assert processed.subscription_id is None


def test_activation_token_links_payment_and_activates(db):
    user = make_user(db, "p5")
    payment = create_pending_payment(
        db,
        user_id=None,
        plan_code="pro",
        duration_months=1,
        provider="website",
        external_payment_id="pay-5",
        amount_minor=599000,
    )
    process_successful_payment(
        db,
        provider="website",
        external_payment_id="pay-5",
        provider_event_id="evt-5",
    )
    token = create_activation_token(db, payment_id=payment.id)
    activated = consume_activation_token(db, token=token, user_id=user.id)
    assert activated.user_id == user.id
    assert activated.subscription_id is not None
    row = db.scalar(select(ActivationToken).where(ActivationToken.payment_id == payment.id))
    assert row.status == "used"
    assert row.used_by_user_id == user.id


def test_activation_token_cannot_be_used_twice(db):
    user = make_user(db, "p6")
    payment = create_pending_payment(
        db,
        user_id=None,
        plan_code="solo",
        duration_months=1,
        provider="website",
        external_payment_id="pay-6",
        amount_minor=249000,
    )
    process_successful_payment(
        db,
        provider="website",
        external_payment_id="pay-6",
        provider_event_id="evt-6",
    )
    token = create_activation_token(db, payment_id=payment.id)
    consume_activation_token(db, token=token, user_id=user.id)
    with pytest.raises(PaymentError, match="недействителен"):
        consume_activation_token(db, token=token, user_id=user.id)


def test_activation_token_expires(db):
    user = make_user(db, "p7")
    payment = create_pending_payment(
        db,
        user_id=None,
        plan_code="solo",
        duration_months=1,
        provider="website",
        external_payment_id="pay-7",
        amount_minor=249000,
    )
    now = datetime(2026, 7, 1, 12, 0, 0)
    process_successful_payment(
        db,
        provider="website",
        external_payment_id="pay-7",
        provider_event_id="evt-7",
        now=now,
    )
    token = create_activation_token(db, payment_id=payment.id, ttl_hours=1, now=now)
    with pytest.raises(PaymentError, match="истёк"):
        consume_activation_token(
            db,
            token=token,
            user_id=user.id,
            now=now + timedelta(hours=2),
        )


def test_user_lead_status_and_notes_are_isolated(db):
    user_one = make_user(db, "u1")
    user_two = make_user(db, "u2")
    lead = make_lead(db)
    save_user_lead(
        db,
        user_id=user_one.id,
        lead_id=lead.id,
        status="contacted",
        notes="Написал в Telegram",
    )
    save_user_lead(
        db,
        user_id=user_two.id,
        lead_id=lead.id,
        status="new",
        notes="Ещё не связывался",
    )
    one = list_user_leads(db, user_id=user_one.id)[0]
    two = list_user_leads(db, user_id=user_two.id)[0]
    assert one.status == "contacted"
    assert two.status == "new"
    assert one.notes != two.notes
    assert db.scalar(select(func.count(UserLead.id))) == 2


def test_update_user_lead_changes_only_owner_record(db):
    user_one = make_user(db, "u3")
    user_two = make_user(db, "u4")
    lead = make_lead(db, "2")
    save_user_lead(db, user_id=user_one.id, lead_id=lead.id)
    save_user_lead(db, user_id=user_two.id, lead_id=lead.id)
    update_user_lead(
        db,
        user_id=user_one.id,
        lead_id=lead.id,
        status="replied",
        notes="Получен ответ",
    )
    assert list_user_leads(db, user_id=user_one.id)[0].status == "replied"
    assert list_user_leads(db, user_id=user_two.id)[0].status == "new"


def test_analytics_redacts_sensitive_parameters(db):
    event = log_analytics_event(
        db,
        platform="telegram",
        event_name="lead_search_started",
        parameters={
            "city": "Москва",
            "token": "very-secret",
            "nested": {"password": "hidden"},
        },
    )
    data = json.loads(event.parameters_json)
    assert data["city"] == "Москва"
    assert data["token"] == "***"
    assert data["nested"]["password"] == "***"
    assert "very-secret" not in event.parameters_json


def test_search_run_lifecycle(db):
    user = make_user(db, "s1")
    started = start_search_run(
        db,
        platform="telegram",
        user_id=user.id,
        niche="косметолог",
        city="Москва",
        parameters={"limit": 5},
    )
    assert started.status == "running"
    finished = finish_search_run(
        db,
        search_run_id=started.id,
        status="success",
        result_count=4,
        duration_ms=1234,
    )
    assert finished.status == "success"
    assert finished.result_count == 4
    assert finished.finished_at is not None
    assert db.scalar(select(func.count(SearchRun.id))) == 1


def test_admin_notification_is_idempotent(db):
    first = queue_admin_notification(
        db,
        notification_type="payment_paid",
        payload={"payment_id": 10},
        idempotency_key="payment_paid:10",
    )
    second = queue_admin_notification(
        db,
        notification_type="payment_paid",
        payload={"payment_id": 10},
        idempotency_key="payment_paid:10",
    )
    assert first.id == second.id
    assert db.scalar(select(func.count(AdminNotification.id))) == 1
