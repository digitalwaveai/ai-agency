from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Plan, PlanPrice, Subscription, UsageEvent, UsagePeriod, UserIdentity
from app.services.plan_service import get_plan_by_code, seed_default_plans
from app.services.subscription_service import (
    SubscriptionError,
    activate_demo_subscription,
    activate_subscription,
    register_identity,
)
from app.services.usage_service import (
    UsageLimitExceeded,
    confirm_usage,
    release_expired_reservations,
    release_usage,
    reserve_usage,
    usage_snapshot,
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


def test_commercial_tables_registered():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    names = set(inspect(engine).get_table_names())
    assert {
        "users",
        "user_identities",
        "plans",
        "plan_prices",
        "subscriptions",
        "usage_periods",
        "usage_events",
    }.issubset(names)


def test_default_plans_and_prices(db):
    plans = db.scalars(select(Plan).order_by(Plan.code)).all()
    assert [plan.code for plan in plans] == ["agency", "demo", "pro", "solo"]
    assert db.scalar(select(Plan).where(Plan.code == "pro")).searches_limit == 120
    assert db.scalar(select(PlanPrice).where(PlanPrice.price_rub == 57490)).duration_months == 12
    assert len(db.scalars(select(PlanPrice)).all()) == 12


def test_register_identity_is_idempotent(db):
    first = register_identity(
        db,
        platform="telegram",
        external_user_id=123,
        username="first_name",
        first_name="Иван",
    )
    second = register_identity(
        db,
        platform="telegram",
        external_user_id="123",
        username="updated_name",
        first_name="Иван",
    )
    assert first.id == second.id
    identities = db.scalars(select(UserIdentity)).all()
    assert len(identities) == 1
    assert identities[0].username == "updated_name"


def test_activate_paid_subscription(db):
    user = register_identity(db, platform="telegram", external_user_id="42")
    now = datetime(2026, 7, 1, 12, 0, 0)
    subscription = activate_subscription(
        db,
        user_id=user.id,
        plan_code="solo",
        duration_months=3,
        source="manual",
        now=now,
    )
    assert subscription.status == "active"
    assert subscription.ends_at == now + timedelta(days=90)
    assert subscription.next_usage_reset_at == now + timedelta(days=30)


def test_demo_can_only_be_activated_once(db):
    user = register_identity(db, platform="discord", external_user_id="777")
    activate_demo_subscription(db, user_id=user.id)
    with pytest.raises(SubscriptionError, match="Demo уже использован"):
        activate_demo_subscription(db, user_id=user.id)


def test_reserve_confirm_and_snapshot(db):
    user = register_identity(db, platform="telegram", external_user_id="101")
    now = datetime(2026, 7, 1, 12, 0, 0)
    subscription = activate_subscription(
        db,
        user_id=user.id,
        plan_code="solo",
        duration_months=1,
        now=now,
    )
    reservation = reserve_usage(
        db,
        subscription,
        resource="searches",
        idempotency_key="search:101:1",
        now=now,
    )
    confirm_usage(db, reservation, now=now)
    snapshot = usage_snapshot(db, subscription, now=now)
    assert snapshot["searches"] == {"used": 1, "limit": 30, "remaining": 29}


def test_idempotency_does_not_double_charge(db):
    user = register_identity(db, platform="telegram", external_user_id="202")
    now = datetime(2026, 7, 1, 12, 0, 0)
    subscription = activate_subscription(
        db,
        user_id=user.id,
        plan_code="solo",
        duration_months=1,
        now=now,
    )
    first = reserve_usage(
        db,
        subscription,
        resource="audits",
        idempotency_key="audit:202:55",
        now=now,
    )
    second = reserve_usage(
        db,
        subscription,
        resource="audits",
        idempotency_key="audit:202:55",
        now=now,
    )
    assert second.already_processed is True
    assert second.event_id == first.event_id
    assert usage_snapshot(db, subscription, now=now)["audits"]["used"] == 1


def test_limit_cannot_be_exceeded(db):
    user = register_identity(db, platform="telegram", external_user_id="303")
    now = datetime(2026, 7, 1, 12, 0, 0)
    subscription = activate_demo_subscription(db, user_id=user.id, now=now)
    first = reserve_usage(db, subscription, resource="searches", now=now)
    confirm_usage(db, first, now=now)
    with pytest.raises(UsageLimitExceeded):
        reserve_usage(db, subscription, resource="searches", now=now)
    assert usage_snapshot(db, subscription, now=now)["searches"]["used"] == 1


def test_release_returns_limit(db):
    user = register_identity(db, platform="telegram", external_user_id="404")
    now = datetime(2026, 7, 1, 12, 0, 0)
    subscription = activate_subscription(
        db,
        user_id=user.id,
        plan_code="solo",
        duration_months=1,
        now=now,
    )
    reservation = reserve_usage(db, subscription, resource="messages", now=now)
    release_usage(db, reservation, reason="backend failed", now=now)
    assert usage_snapshot(db, subscription, now=now)["messages"]["used"] == 0
    assert len(db.scalars(select(UsageEvent)).all()) == 2


def test_expired_reservation_is_released(db):
    user = register_identity(db, platform="telegram", external_user_id="505")
    now = datetime(2026, 7, 1, 12, 0, 0)
    subscription = activate_subscription(
        db,
        user_id=user.id,
        plan_code="solo",
        duration_months=1,
        now=now,
    )
    reserve_usage(
        db,
        subscription,
        resource="searches",
        reservation_minutes=1,
        now=now,
    )
    released = release_expired_reservations(db, now=now + timedelta(minutes=2))
    assert released == 1
    assert usage_snapshot(db, subscription, now=now + timedelta(minutes=2))["searches"]["used"] == 0


def test_new_usage_period_after_thirty_days(db):
    user = register_identity(db, platform="telegram", external_user_id="606")
    start = datetime(2026, 7, 1, 12, 0, 0)
    subscription = activate_subscription(
        db,
        user_id=user.id,
        plan_code="pro",
        duration_months=3,
        now=start,
    )
    first = reserve_usage(db, subscription, resource="searches", now=start)
    confirm_usage(db, first, now=start)

    second_period_time = start + timedelta(days=31)
    snapshot = usage_snapshot(db, subscription, now=second_period_time)
    assert snapshot["searches"]["used"] == 0
    assert len(db.scalars(select(UsagePeriod)).all()) == 2
