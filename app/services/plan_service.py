from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Plan, PlanPrice
from app.services.pricing_service import (
    PRODUCTION_RUB_PRICES,
    seed_pricing_profiles,
)


PAID_DURATION_MONTHS = (1, 3, 6, 12)
LEGACY_PLAN_CODES = {"demo", "solo", "agency"}
PURCHASE_PLAN_ALIASES = {
    "solo": "standard",
    "agency": "pro",
}

PLAN_PROJECT_LIMITS: dict[str, int | None] = {
    "trial": 1,
    "standard": 3,
    "pro": 10,
    # Compatibility for already-active legacy subscriptions.
    "demo": 1,
    "solo": 3,
    "agency": 30,
}

PLAN_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "code": "trial",
        "name": "Пробный",
        "description": "7 дней бесплатно, чтобы проверить весь LeadPilot AI",
        "searches_limit": 20,
        "saved_leads_limit": 20,
        "audits_limit": 20,
        "messages_limit": 20,
        "radars_limit": 0,
        "export_enabled": True,
        "analytics_enabled": True,
        "prices": {},
    },
    {
        "code": "standard",
        "name": "Стандарт",
        "description": "Для фрилансеров, экспертов и небольших команд",
        "searches_limit": 100,
        "saved_leads_limit": 100,
        "audits_limit": 100,
        "messages_limit": 100,
        "radars_limit": 3,
        "export_enabled": True,
        "analytics_enabled": True,
        "prices": {
            duration: (amount_minor // 100, discount)
            for duration, (amount_minor, discount) in PRODUCTION_RUB_PRICES[
                "standard"
            ].items()
        },
    },
    {
        "code": "pro",
        "name": "Pro",
        "description": "Для активного поиска клиентов и нескольких направлений",
        "searches_limit": 500,
        "saved_leads_limit": 500,
        "audits_limit": 500,
        "messages_limit": 500,
        "radars_limit": 10,
        "export_enabled": True,
        "analytics_enabled": True,
        "prices": {
            duration: (amount_minor // 100, discount)
            for duration, (amount_minor, discount) in PRODUCTION_RUB_PRICES[
                "pro"
            ].items()
        },
    },
)


def get_plan_by_code(db: Session, code: str) -> Plan | None:
    return db.scalar(select(Plan).where(Plan.code == code.strip().lower()))


def normalize_purchase_plan_code(code: str) -> str:
    normalized = code.strip().lower()
    return PURCHASE_PLAN_ALIASES.get(normalized, normalized)


def get_plan_price(
    db: Session,
    plan: Plan,
    duration_months: int,
) -> PlanPrice | None:
    return db.scalar(
        select(PlanPrice).where(
            PlanPrice.plan_id == plan.id,
            PlanPrice.duration_months == duration_months,
            PlanPrice.is_active.is_(True),
        )
    )


def get_project_limit(plan_code: str) -> int | None:
    return PLAN_PROJECT_LIMITS.get(plan_code.strip().lower())


def seed_default_plans(db: Session, *, commit: bool = True) -> list[Plan]:
    """Create the current catalog and retire legacy plans for new purchases.

    Legacy plan rows are kept for history and existing subscriptions. Their
    prices are disabled, while usage for already-active subscriptions remains
    valid in usage_service.
    """

    legacy_plans = db.scalars(select(Plan).where(Plan.code.in_(LEGACY_PLAN_CODES))).all()
    for plan in legacy_plans:
        plan.is_active = False
        for price in db.scalars(select(PlanPrice).where(PlanPrice.plan_id == plan.id)).all():
            price.is_active = False

    seeded: list[Plan] = []

    for item in PLAN_CATALOG:
        plan = get_plan_by_code(db, item["code"])
        if plan is None:
            plan = Plan(code=item["code"], name=item["name"])
            db.add(plan)

        for field in (
            "name",
            "description",
            "searches_limit",
            "saved_leads_limit",
            "audits_limit",
            "messages_limit",
            "radars_limit",
            "export_enabled",
            "analytics_enabled",
        ):
            setattr(plan, field, item[field])
        plan.is_active = True
        db.flush()

        desired_durations = set(item["prices"])
        existing_prices = db.scalars(
            select(PlanPrice).where(PlanPrice.plan_id == plan.id)
        ).all()
        for existing in existing_prices:
            if existing.duration_months not in desired_durations:
                existing.is_active = False

        for duration_months, (price_rub, discount_percent) in item["prices"].items():
            price = db.scalar(
                select(PlanPrice).where(
                    PlanPrice.plan_id == plan.id,
                    PlanPrice.duration_months == duration_months,
                )
            )
            if price is None:
                price = PlanPrice(
                    plan_id=plan.id,
                    duration_months=duration_months,
                    price_rub=price_rub,
                )
                db.add(price)
            price.price_rub = price_rub
            price.discount_percent = discount_percent
            price.is_active = True

        seeded.append(plan)

    # Price profiles are seeded only once. Later owner edits are preserved and
    # the selected profile is re-applied after every restart.
    seed_pricing_profiles(db, commit=False)

    if commit:
        db.commit()
        for plan in seeded:
            db.refresh(plan)
    else:
        db.flush()

    return seeded
