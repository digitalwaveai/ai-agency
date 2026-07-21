from datetime import datetime, timedelta
import json

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Payment, Plan, PlanPrice, Subscription
from app.services.access_service import (
    ensure_owner_access,
    grant_admin_by_owner,
)
from app.services.entitlement_service import project_limit_for_user
from app.services.payment_service import (
    PaymentError,
    create_pending_payment,
    process_successful_payment,
)
from app.services.plan_service import seed_default_plans
from app.services.pricing_service import (
    PricingError,
    get_active_profile_price,
    get_pricing_state,
    set_active_pricing_profile,
    set_profile_price,
)
from app.services.subscription_service import register_identity


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


def make_owner(db, external_id: str = "owner"):
    owner = register_identity(
        db,
        platform="telegram",
        external_user_id=external_id,
    )
    ensure_owner_access(db, user_id=owner.id)
    return owner


def test_pricing_tables_and_default_profiles_are_seeded(db):
    table_names = set(inspect(engine).get_table_names())
    assert {"pricing_settings", "pricing_profile_prices"}.issubset(table_names)
    assert get_pricing_state(db).active_profile == "production"

    expected = {
        "standard": {
            1: {"RUB": 99_000, "XTR": 970},
            3: {"RUB": 279_000, "XTR": 2_740},
            6: {"RUB": 539_000, "XTR": 5_290},
            12: {"RUB": 999_000, "XTR": 9_810},
        },
        "pro": {
            1: {"RUB": 249_000, "XTR": 2_450},
            3: {"RUB": 699_000, "XTR": 6_870},
            6: {"RUB": 1_349_000, "XTR": 13_250},
            12: {"RUB": 2_499_000, "XTR": 24_550},
        },
    }
    for plan_code, durations in expected.items():
        for duration_months, currencies in durations.items():
            for currency, amount_minor in currencies.items():
                price = get_active_profile_price(
                    db,
                    plan_code=plan_code,
                    duration_months=duration_months,
                    currency=currency,
                )
                assert price is not None
                assert price.amount_minor == amount_minor


def test_only_owner_can_switch_price_profile(db):
    owner = make_owner(db)
    admin = register_identity(db, platform="telegram", external_user_id="admin")
    grant_admin_by_owner(
        db,
        owner_user_id=owner.id,
        target_user_id=admin.id,
    )

    with pytest.raises(PricingError, match="Только владелец"):
        set_active_pricing_profile(
            db,
            owner_user_id=admin.id,
            profile_code="test",
        )

    state = set_active_pricing_profile(
        db,
        owner_user_id=owner.id,
        profile_code="test",
    )
    assert state.active_profile == "test"


def test_test_profile_survives_restart_seed_and_restores_production(db):
    owner = make_owner(db)
    set_active_pricing_profile(
        db,
        owner_user_id=owner.id,
        profile_code="test",
    )

    prices = db.scalars(select(PlanPrice).where(PlanPrice.is_active.is_(True))).all()
    assert prices
    assert {price.price_rub for price in prices} == {1}

    # Bot startup calls this again; the selected profile must not be reset.
    seed_default_plans(db)
    assert get_pricing_state(db).active_profile == "test"
    prices = db.scalars(select(PlanPrice).where(PlanPrice.is_active.is_(True))).all()
    assert {price.price_rub for price in prices} == {1}

    set_active_pricing_profile(
        db,
        owner_user_id=owner.id,
        profile_code="production",
    )
    standard = db.scalar(
        select(PlanPrice)
        .join(Plan, Plan.id == PlanPrice.plan_id)
        .where(Plan.code == "standard", PlanPrice.duration_months == 1)
    )
    assert standard.price_rub == 990


def test_owner_can_edit_presets_without_redeploy(db):
    owner = make_owner(db)
    row = set_profile_price(
        db,
        owner_user_id=owner.id,
        profile_code="production",
        plan_code="standard",
        duration_months=1,
        currency="RUB",
        amount_minor=123_400,
    )
    assert row.amount_minor == 123_400

    legacy = db.scalar(
        select(PlanPrice)
        .join(Plan, Plan.id == PlanPrice.plan_id)
        .where(Plan.code == "standard", PlanPrice.duration_months == 1)
    )
    assert legacy.price_rub == 1234

    seed_default_plans(db)
    configured = get_active_profile_price(
        db,
        plan_code="standard",
        duration_months=1,
        currency="RUB",
    )
    assert configured.amount_minor == 123_400
    assert legacy.price_rub == 1234


def test_existing_invoice_keeps_snapshot_after_profile_switch(db):
    owner = make_owner(db)
    user = register_identity(db, platform="telegram", external_user_id="buyer")
    production = create_pending_payment(
        db,
        user_id=user.id,
        plan_code="standard",
        duration_months=1,
        provider="website",
        external_payment_id="production-payment",
        amount_minor=99_000,
    )
    production_snapshot = json.loads(production.metadata_json)["_pricing"]
    assert production_snapshot["profile"] == "production"
    assert production_snapshot["configured_amount_minor"] == 99_000

    set_active_pricing_profile(
        db,
        owner_user_id=owner.id,
        profile_code="test",
    )
    db.refresh(production)
    assert production.amount_minor == 99_000
    assert json.loads(production.metadata_json)["_pricing"] == production_snapshot

    test_payment = create_pending_payment(
        db,
        user_id=user.id,
        plan_code="standard",
        duration_months=1,
        provider="website",
        external_payment_id="test-payment",
        amount_minor=100,
    )
    assert json.loads(test_payment.metadata_json)["_pricing"]["profile"] == "test"


def test_one_star_payment_activates_term_and_normal_limits(db):
    start = datetime(2026, 7, 20, 12, 0, 0)
    owner = make_owner(db)
    user = register_identity(db, platform="telegram", external_user_id="stars-buyer")
    set_active_pricing_profile(
        db,
        owner_user_id=owner.id,
        profile_code="test",
        now=start,
    )

    with pytest.raises(PaymentError, match="ожидалось 1 XTR"):
        create_pending_payment(
            db,
            user_id=user.id,
            plan_code="standard",
            duration_months=3,
            provider="telegram_stars",
            external_payment_id="wrong-stars",
            amount_minor=2,
            currency="XTR",
            now=start,
        )

    payment = create_pending_payment(
        db,
        user_id=user.id,
        plan_code="standard",
        duration_months=3,
        provider="telegram_stars",
        external_payment_id="one-star",
        amount_minor=1,
        currency="XTR",
        now=start,
    )
    processed, _ = process_successful_payment(
        db,
        provider="telegram_stars",
        external_payment_id="one-star",
        provider_event_id="stars-event",
        now=start,
    )
    assert processed.id == payment.id
    subscription = db.get(Subscription, processed.subscription_id)
    assert subscription.ends_at == start + timedelta(days=90)
    assert project_limit_for_user(db, user_id=user.id, now=start) == 3
    assert db.scalar(select(Payment).where(Payment.id == payment.id)).amount_minor == 1


def test_production_xtr_price_can_be_configured_above_recurring_limit(db):
    owner = make_owner(db)
    set_profile_price(
        db,
        owner_user_id=owner.id,
        profile_code="production",
        plan_code="pro",
        duration_months=1,
        currency="XTR",
        amount_minor=26_000,
    )
    configured = get_active_profile_price(
        db,
        plan_code="pro",
        duration_months=1,
        currency="XTR",
    )
    assert configured.amount_minor == 26_000
