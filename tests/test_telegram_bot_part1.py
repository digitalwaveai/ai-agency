from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Subscription, UserLead
from app.services.access_service import grant_beta_access
from app.services.plan_service import seed_default_plans
from app.services.subscription_service import register_identity
from app.services.telegram_service import (
    TelegramServiceError,
    format_account_text,
    format_limits_text,
    format_plan_catalog,
    parse_access_duration,
    register_telegram_account,
    user_leads_count,
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


def test_parse_access_duration_supports_days_and_unlimited():
    assert parse_access_duration("30") == 30
    assert parse_access_duration("unlimited") is None
    assert parse_access_duration("бессрочно") is None


def test_parse_access_duration_rejects_invalid_value():
    with pytest.raises(TelegramServiceError):
        parse_access_duration("0")
    with pytest.raises(TelegramServiceError):
        parse_access_duration("abc")


def test_first_start_creates_demo(db):
    account = register_telegram_account(
        db,
        telegram_id=10001,
        username="first_user",
        first_name="Анна",
    )
    assert account.demo_created is True
    assert account.plan is not None
    assert account.plan.code == "demo"
    assert account.access.role == "customer"


def test_second_start_does_not_duplicate_demo(db):
    first = register_telegram_account(db, telegram_id=10002)
    second = register_telegram_account(db, telegram_id=10002)
    assert first.user.id == second.user.id
    assert second.demo_created is False
    assert db.scalar(select(func.count(Subscription.id))) == 1


def test_owner_id_becomes_admin_and_skips_demo(db):
    account = register_telegram_account(
        db,
        telegram_id=777,
        admin_telegram_id=777,
    )
    assert account.user.is_admin is True
    assert account.access.role == "admin"
    assert account.access.unlimited is True
    assert account.subscription is None


def test_beta_tester_keeps_unlimited_access_without_demo(db):
    user = register_identity(
        db,
        platform="telegram",
        external_user_id=10003,
    )
    grant_beta_access(
        db,
        user_id=user.id,
        duration_days=14,
        now=datetime.utcnow(),
    )
    account = register_telegram_account(db, telegram_id=10003)
    assert account.access.role == "beta_tester"
    assert account.access.unlimited is True
    assert account.demo_created is False
    assert account.subscription is None


def test_plan_catalog_contains_all_paid_plans_and_prices(db):
    text = format_plan_catalog(db)
    assert "Solo" in text
    assert "Pro" in text
    assert "Agency" in text
    assert "2 490 ₽" in text
    assert "57 490 ₽" in text
    assert "114 990 ₽" in text


def test_limits_text_for_demo_and_admin(db):
    customer = register_telegram_account(db, telegram_id=10004)
    customer_text = format_limits_text(db, customer.user.id)
    assert "Demo" in customer_text
    assert "0 / 1" in customer_text

    admin = register_telegram_account(
        db,
        telegram_id=10005,
        admin_telegram_id=10005,
    )
    admin_text = format_limits_text(db, admin.user.id)
    assert "не применяются" in admin_text
    assert "Администратор" in admin_text


def test_account_text_shows_beta_expiry(db):
    user = register_identity(
        db,
        platform="telegram",
        external_user_id=10006,
    )
    now = datetime(2026, 7, 1, 12, 0, 0)
    grant_beta_access(db, user_id=user.id, duration_days=7, now=now)
    account = register_telegram_account(
        db,
        telegram_id=10006,
        now=now + timedelta(days=1),
    )
    text = format_account_text(account)
    assert "Beta Tester" in text
    assert "08.07.2026" in text


def test_user_leads_count_is_user_specific(db):
    first = register_telegram_account(db, telegram_id=10007)
    second = register_telegram_account(db, telegram_id=10008)

    db.add(
        UserLead(
            user_id=first.user.id,
            lead_id=1,
            status="new",
        )
    )
    db.commit()

    assert user_leads_count(db, first.user.id) == 1
    assert user_leads_count(db, second.user.id) == 0
