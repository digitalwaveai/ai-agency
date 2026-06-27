from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Plan, PlanPrice, Subscription, User, UserLead
from app.services.access_service import AccessState, get_effective_access
from app.services.plan_service import seed_default_plans
from app.services.subscription_service import (
    SubscriptionError,
    activate_demo_subscription,
    get_active_subscription,
    register_identity,
)
from app.services.usage_service import usage_snapshot


ROLE_LABELS = {
    "admin": "Администратор",
    "beta_tester": "Beta Tester",
    "customer": "Клиент",
}

RESOURCE_LABELS = {
    "searches": "Поиски",
    "saved_leads": "Сохранённые лиды",
    "audits": "Паспорта",
    "messages": "Сообщения",
}


class TelegramServiceError(ValueError):
    pass


@dataclass(frozen=True)
class TelegramAccount:
    user: User
    access: AccessState
    subscription: Subscription | None
    plan: Plan | None
    demo_created: bool


def parse_access_duration(value: str) -> int | None:
    normalized = value.strip().lower()
    if normalized in {"unlimited", "forever", "бессрочно", "навсегда"}:
        return None

    try:
        days = int(normalized)
    except ValueError as exc:
        raise TelegramServiceError(
            "Укажите срок числом от 1 до 3650 дней или unlimited"
        ) from exc

    if not 1 <= days <= 3650:
        raise TelegramServiceError("Срок должен быть от 1 до 3650 дней")
    return days


def register_telegram_account(
    db: Session,
    *,
    telegram_id: int | str,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    admin_telegram_id: int | str | None = None,
    now: datetime | None = None,
) -> TelegramAccount:
    now = now or datetime.utcnow()
    seed_default_plans(db)

    user = register_identity(
        db,
        platform="telegram",
        external_user_id=telegram_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        display_name=" ".join(
            part for part in (first_name, last_name) if part
        ).strip() or username,
        now=now,
    )

    if (
        admin_telegram_id is not None
        and str(telegram_id).strip() == str(admin_telegram_id).strip()
        and not user.is_admin
    ):
        user.is_admin = True
        db.commit()
        db.refresh(user)

    access = get_effective_access(db, user.id, now=now)
    subscription = get_active_subscription(db, user.id, now=now)
    demo_created = False

    if not access.unlimited and subscription is None:
        try:
            subscription = activate_demo_subscription(
                db,
                user_id=user.id,
                now=now,
            )
            demo_created = True
        except SubscriptionError as exc:
            if "Demo уже использован" not in str(exc):
                raise

    access = get_effective_access(db, user.id, now=now)
    subscription = get_active_subscription(db, user.id, now=now)
    plan = db.get(Plan, subscription.plan_id) if subscription else None

    return TelegramAccount(
        user=user,
        access=access,
        subscription=subscription,
        plan=plan,
        demo_created=demo_created,
    )


def format_account_text(account: TelegramAccount) -> str:
    role = ROLE_LABELS.get(account.access.role, account.access.role)
    lines = ["👤 <b>Ваш аккаунт</b>", "", f"Роль: <b>{role}</b>"]

    if account.access.unlimited:
        lines.append("Доступ: <b>без лимитов тарифа</b>")
        if account.access.ends_at is None:
            lines.append("Срок: <b>бессрочно</b>")
        else:
            lines.append(
                "Действует до: "
                f"<b>{account.access.ends_at.strftime('%d.%m.%Y %H:%M')}</b>"
            )
    elif account.subscription and account.plan:
        lines.extend(
            [
                f"Тариф: <b>{account.plan.name}</b>",
                "Подписка до: "
                f"<b>{account.subscription.ends_at.strftime('%d.%m.%Y')}</b>",
            ]
        )
    else:
        lines.extend(
            ["Тариф: <b>не активен</b>", "Откройте раздел «⭐ Тарифы»."]
        )

    if account.demo_created:
        lines.extend(
            ["", "🎁 Вам автоматически активирован пробный тариф Demo."]
        )

    return "\n".join(lines)


def format_limits_text(db: Session, user_id: int) -> str:
    access = get_effective_access(db, user_id)

    if access.unlimited:
        role = ROLE_LABELS.get(access.role, access.role)
        lines = [
            "📊 <b>Ваши лимиты</b>",
            "",
            f"Роль: <b>{role}</b>",
            "Лимиты тарифа: <b>не применяются</b>",
        ]
        if access.ends_at is None:
            lines.append("Срок доступа: <b>бессрочно</b>")
        else:
            lines.append(
                "Доступ до: "
                f"<b>{access.ends_at.strftime('%d.%m.%Y %H:%M')}</b>"
            )
        return "\n".join(lines)

    subscription = get_active_subscription(db, user_id)
    if subscription is None:
        return (
            "📊 <b>Ваши лимиты</b>\n\n"
            "Активной подписки нет. Откройте раздел «⭐ Тарифы»."
        )

    plan = db.get(Plan, subscription.plan_id)
    if plan is None:
        raise TelegramServiceError("Тариф подписки не найден")

    snapshot = usage_snapshot(db, subscription)
    db.commit()

    lines = [
        "📊 <b>Ваши лимиты</b>",
        "",
        f"Тариф: <b>{plan.name}</b>",
        f"Период до: <b>{subscription.ends_at.strftime('%d.%m.%Y')}</b>",
        "",
    ]
    for resource in ("searches", "saved_leads", "audits", "messages"):
        values = snapshot[resource]
        lines.append(
            f"{RESOURCE_LABELS[resource]}: "
            f"<b>{values['used']} / {values['limit']}</b> "
            f"(осталось {values['remaining']})"
        )
    lines.append(f"Активные радары: до <b>{plan.radars_limit}</b>")
    return "\n".join(lines)


def format_plan_catalog(db: Session) -> str:
    seed_default_plans(db)
    plans = list(
        db.scalars(
            select(Plan)
            .where(Plan.code.in_(("solo", "pro", "agency")))
            .order_by(Plan.id)
        ).all()
    )

    lines = [
        "⭐ <b>Тарифы Beauty Lead Finder</b>",
        "",
        "Лимиты обновляются каждые 30 дней.",
        "",
    ]

    for plan in plans:
        prices = list(
            db.scalars(
                select(PlanPrice)
                .where(
                    PlanPrice.plan_id == plan.id,
                    PlanPrice.is_active.is_(True),
                )
                .order_by(PlanPrice.duration_months)
            ).all()
        )
        badge = " — ⭐ рекомендуемый" if plan.code == "pro" else ""
        lines.extend(
            [
                f"<b>{plan.name}{badge}</b>",
                plan.description,
                (
                    f"Поиски: {plan.searches_limit} · "
                    f"Лиды: {plan.saved_leads_limit} · "
                    f"Паспорта: {plan.audits_limit} · "
                    f"Радары: {plan.radars_limit}"
                ),
            ]
        )
        for price in prices:
            discount = (
                f" (−{price.discount_percent}%)"
                if price.discount_percent
                else ""
            )
            formatted = f"{price.price_rub:,}".replace(",", " ")
            lines.append(
                f"• {price.duration_months} мес.: "
                f"<b>{formatted} ₽</b>{discount}"
            )
        lines.append("")

    lines.extend(
        [
            "Оплату подключим после завершения пользовательского интерфейса.",
            "Внутри Telegram — Telegram Stars; на сайте — ЮKassa.",
        ]
    )
    return "\n".join(lines)


def user_leads_count(db: Session, user_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(UserLead.id)).where(UserLead.user_id == user_id)
        )
        or 0
    )
