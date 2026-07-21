from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AccessGrant, Plan, UserProject
from app.services.access_service import (
    AccessError,
    ensure_owner_access,
    get_effective_access,
    grant_admin_by_owner,
    revoke_admin_by_owner,
)
from app.services.entitlement_service import (
    EntitlementError,
    ensure_project_capacity,
    project_limit_for_user,
)
from app.services.plan_service import seed_default_plans
from app.services.subscription_service import (
    activate_subscription,
    activate_trial_subscription,
    register_identity,
)
from app.services.usage_service import reserve_user_usage


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


def test_owner_is_unique_per_system(db):
    first = register_identity(db, platform="telegram", external_user_id="1")
    second = register_identity(db, platform="telegram", external_user_id="2")
    ensure_owner_access(db, user_id=first.id)
    assert get_effective_access(db, first.id).role == "owner"

    ensure_owner_access(db, user_id=second.id)
    assert get_effective_access(db, second.id).role == "owner"
    assert get_effective_access(db, first.id).role == "customer"
    active_owners = db.scalars(
        select(AccessGrant).where(
            AccessGrant.role == "owner",
            AccessGrant.status == "active",
        )
    ).all()
    assert [grant.user_id for grant in active_owners] == [second.id]


def test_only_owner_can_grant_and_revoke_admin(db):
    owner = register_identity(db, platform="telegram", external_user_id="10")
    ordinary = register_identity(db, platform="telegram", external_user_id="11")
    target = register_identity(db, platform="telegram", external_user_id="12")
    ensure_owner_access(db, user_id=owner.id)

    with pytest.raises(AccessError, match="Только владелец"):
        grant_admin_by_owner(
            db,
            owner_user_id=ordinary.id,
            target_user_id=target.id,
        )

    grant_admin_by_owner(
        db,
        owner_user_id=owner.id,
        target_user_id=target.id,
    )
    assert get_effective_access(db, target.id).role == "admin"
    assert get_effective_access(db, target.id).unlimited is True

    with pytest.raises(AccessError, match="Только владелец"):
        revoke_admin_by_owner(
            db,
            owner_user_id=ordinary.id,
            target_user_id=target.id,
        )

    assert revoke_admin_by_owner(
        db,
        owner_user_id=owner.id,
        target_user_id=target.id,
    ) == 1
    assert get_effective_access(db, target.id).role == "customer"


def test_owner_cannot_be_demoted_or_revoked(db):
    owner = register_identity(db, platform="telegram", external_user_id="20")
    ensure_owner_access(db, user_id=owner.id)
    with pytest.raises(AccessError, match="Владелец уже"):
        grant_admin_by_owner(
            db,
            owner_user_id=owner.id,
            target_user_id=owner.id,
        )
    with pytest.raises(AccessError, match="Роль владельца"):
        revoke_admin_by_owner(
            db,
            owner_user_id=owner.id,
            target_user_id=owner.id,
        )


def test_owner_and_admin_bypass_all_usage_limits(db):
    owner = register_identity(db, platform="telegram", external_user_id="30")
    admin = register_identity(db, platform="telegram", external_user_id="31")
    ensure_owner_access(db, user_id=owner.id)
    grant_admin_by_owner(
        db,
        owner_user_id=owner.id,
        target_user_id=admin.id,
    )

    owner_reservation = reserve_user_usage(
        db,
        user_id=owner.id,
        resource="saved_leads",
        amount=100000,
    )
    admin_reservation = reserve_user_usage(
        db,
        user_id=admin.id,
        resource="saved_leads",
        amount=100000,
    )
    assert owner_reservation.bypassed is True
    assert owner_reservation.access_role == "owner"
    assert admin_reservation.bypassed is True
    assert admin_reservation.access_role == "admin"


def test_project_limits_trial_standard_pro_and_unlimited_roles(db):
    now = datetime(2026, 7, 1, 12, 0, 0)
    trial_user = register_identity(db, platform="telegram", external_user_id="40")
    standard_user = register_identity(db, platform="telegram", external_user_id="41")
    pro_user = register_identity(db, platform="telegram", external_user_id="42")
    owner = register_identity(db, platform="telegram", external_user_id="43")
    admin = register_identity(db, platform="telegram", external_user_id="44")

    activate_trial_subscription(db, user_id=trial_user.id, now=now)
    activate_subscription(
        db,
        user_id=standard_user.id,
        plan_code="standard",
        duration_months=1,
        now=now,
    )
    activate_subscription(
        db,
        user_id=pro_user.id,
        plan_code="pro",
        duration_months=1,
        now=now,
    )
    ensure_owner_access(db, user_id=owner.id, now=now)
    grant_admin_by_owner(
        db,
        owner_user_id=owner.id,
        target_user_id=admin.id,
        now=now,
    )

    assert project_limit_for_user(db, user_id=trial_user.id, now=now) == 1
    assert project_limit_for_user(db, user_id=standard_user.id, now=now) == 3
    assert project_limit_for_user(db, user_id=pro_user.id, now=now) == 10
    assert project_limit_for_user(db, user_id=owner.id, now=now) is None
    assert project_limit_for_user(db, user_id=admin.id, now=now) is None

    db.add(UserProject(user_id=trial_user.id, name="Trial project"))
    db.commit()
    with pytest.raises(EntitlementError, match="1 из 1"):
        ensure_project_capacity(db, user_id=trial_user.id, now=now)


def test_project_creation_requires_subscription_for_customer(db):
    user = register_identity(db, platform="telegram", external_user_id="50")
    with pytest.raises(EntitlementError, match="Нет активной подписки"):
        ensure_project_capacity(db, user_id=user.id)
