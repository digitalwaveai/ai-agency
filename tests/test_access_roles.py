from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AccessGrant, UsageEvent
from app.services.access_service import (
    AccessError,
    get_effective_access,
    grant_admin_access,
    grant_beta_access,
    revoke_access,
)
from app.services.plan_service import seed_default_plans
from app.services.subscription_service import activate_demo_subscription, register_identity
from app.services.usage_service import (
    UsageError,
    confirm_usage,
    reserve_user_usage,
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


def test_access_grants_table_registered():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    assert "access_grants" in set(inspect(engine).get_table_names())


def test_beta_access_custom_duration(db):
    user = register_identity(db, platform="telegram", external_user_id="1001")
    now = datetime(2026, 7, 1, 12, 0, 0)
    grant = grant_beta_access(db, user_id=user.id, duration_days=14, now=now)
    state = get_effective_access(db, user.id, now=now + timedelta(days=13))
    assert grant.ends_at == now + timedelta(days=14)
    assert state.role == "beta_tester"
    assert state.unlimited is True


def test_beta_access_can_be_unlimited(db):
    user = register_identity(db, platform="telegram", external_user_id="1002")
    grant = grant_beta_access(db, user_id=user.id, duration_days=None)
    state = get_effective_access(db, user.id)
    assert grant.ends_at is None
    assert state.role == "beta_tester"


def test_beta_access_expires_automatically(db):
    user = register_identity(db, platform="telegram", external_user_id="1003")
    now = datetime(2026, 7, 1, 12, 0, 0)
    grant_beta_access(db, user_id=user.id, duration_days=7, now=now)
    state = get_effective_access(db, user.id, now=now + timedelta(days=8))
    grant = db.scalar(select(AccessGrant).where(AccessGrant.user_id == user.id))
    assert state.role == "customer"
    assert grant.status == "expired"


def test_revoke_beta_access(db):
    user = register_identity(db, platform="discord", external_user_id="2001")
    grant_beta_access(db, user_id=user.id, duration_days=30)
    assert revoke_access(db, user_id=user.id, role="beta_tester") == 1
    assert get_effective_access(db, user.id).role == "customer"


def test_admin_has_priority_and_cannot_be_downgraded(db):
    user = register_identity(db, platform="telegram", external_user_id="1004")
    grant_admin_access(db, user_id=user.id, duration_days=None)
    assert get_effective_access(db, user.id).role == "admin"
    with pytest.raises(AccessError, match="Нельзя заменить"):
        grant_beta_access(db, user_id=user.id, duration_days=30)


def test_beta_user_bypasses_subscription_and_limits(db):
    user = register_identity(db, platform="telegram", external_user_id="1005")
    grant_beta_access(db, user_id=user.id, duration_days=30)
    reservation = reserve_user_usage(
        db,
        user_id=user.id,
        resource="searches",
        amount=500,
    )
    assert reservation.bypassed is True
    assert reservation.access_role == "beta_tester"
    assert confirm_usage(db, reservation) is None
    assert db.scalars(select(UsageEvent)).all() == []


def test_customer_without_subscription_is_denied(db):
    user = register_identity(db, platform="telegram", external_user_id="1006")
    with pytest.raises(UsageError, match="Нет активной подписки"):
        reserve_user_usage(db, user_id=user.id, resource="searches")


def test_customer_with_subscription_uses_normal_limit(db):
    user = register_identity(db, platform="telegram", external_user_id="1007")
    activate_demo_subscription(db, user_id=user.id)
    reservation = reserve_user_usage(db, user_id=user.id, resource="searches")
    assert reservation.bypassed is False
    assert reservation.event_id is not None
    assert confirm_usage(db, reservation).status == "consumed"
