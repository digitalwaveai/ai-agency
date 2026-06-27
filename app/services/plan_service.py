from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Plan, PlanPrice


PLAN_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "code": "demo",
        "name": "Demo",
        "description": "Однократная бесплатная проба",
        "searches_limit": 1,
        "saved_leads_limit": 5,
        "audits_limit": 1,
        "messages_limit": 3,
        "radars_limit": 0,
        "export_enabled": False,
        "analytics_enabled": False,
        "prices": {},
    },
    {
        "code": "solo",
        "name": "Solo",
        "description": "Для специалиста или небольшой студии",
        "searches_limit": 30,
        "saved_leads_limit": 150,
        "audits_limit": 20,
        "messages_limit": 60,
        "radars_limit": 2,
        "export_enabled": False,
        "analytics_enabled": False,
        "prices": {
            1: (2490, 0),
            3: (7090, 5),
            6: (13490, 10),
            12: (23990, 20),
        },
    },
    {
        "code": "pro",
        "name": "Pro",
        "description": "Основной тариф для активного поиска клиентов",
        "searches_limit": 120,
        "saved_leads_limit": 700,
        "audits_limit": 100,
        "messages_limit": 300,
        "radars_limit": 10,
        "export_enabled": True,
        "analytics_enabled": True,
        "prices": {
            1: (5990, 0),
            3: (17090, 5),
            6: (32390, 10),
            12: (57490, 20),
        },
    },
    {
        "code": "agency",
        "name": "Agency",
        "description": "Для агентств и активных отделов продаж",
        "searches_limit": 400,
        "saved_leads_limit": 2500,
        "audits_limit": 300,
        "messages_limit": 1000,
        "radars_limit": 30,
        "export_enabled": True,
        "analytics_enabled": True,
        "prices": {
            1: (11990, 0),
            3: (34190, 5),
            6: (64790, 10),
            12: (114990, 20),
        },
    },
)


def get_plan_by_code(db: Session, code: str) -> Plan | None:
    return db.scalar(select(Plan).where(Plan.code == code.strip().lower()))


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


def seed_default_plans(db: Session, *, commit: bool = True) -> list[Plan]:
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

    if commit:
        db.commit()
        for plan in seeded:
            db.refresh(plan)
    else:
        db.flush()

    return seeded
