from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Plan,
    PlanPrice,
    PricingProfilePrice,
    PricingSettings,
)
from app.services.access_service import get_effective_access


PRICING_PROFILES = {"production", "test"}
SUPPORTED_PRICE_CURRENCIES = {"RUB", "XTR"}
PAID_DURATION_MONTHS = (1, 3, 6, 12)
PLAN_CODE_ALIASES = {"solo": "standard", "agency": "pro"}

# RUB amounts are stored in kopecks here. The public catalog still displays
# whole rubles through the legacy PlanPrice table for backward compatibility.
PRODUCTION_RUB_PRICES: dict[str, dict[int, tuple[int, int]]] = {
    "standard": {
        1: (99_000, 0),
        3: (279_000, 6),
        6: (539_000, 9),
        12: (999_000, 16),
    },
    "pro": {
        1: (249_000, 0),
        3: (699_000, 6),
        6: (1_349_000, 10),
        12: (2_499_000, 16),
    },
}

# XTR amounts are whole Telegram Stars. Purchases for 1, 3, 6 and 12 months
# are one-time invoices; recurring Telegram subscriptions are not used.
PRODUCTION_XTR_PRICES: dict[str, dict[int, int]] = {
    "standard": {
        1: 970,
        3: 2_740,
        6: 5_290,
        12: 9_810,
    },
    "pro": {
        1: 2_450,
        3: 6_870,
        6: 13_250,
        12: 24_550,
    },
}


class PricingError(ValueError):
    pass


@dataclass(frozen=True)
class PricingState:
    active_profile: str
    updated_by_user_id: int | None
    updated_at: datetime


def _normalize_profile(profile_code: str) -> str:
    normalized = profile_code.strip().lower()
    if normalized not in PRICING_PROFILES:
        raise PricingError("Режим цен должен быть production или test")
    return normalized


def _normalize_plan_code(plan_code: str) -> str:
    normalized = plan_code.strip().lower()
    return PLAN_CODE_ALIASES.get(normalized, normalized)


def _normalize_currency(currency: str) -> str:
    normalized = currency.strip().upper()
    if normalized not in SUPPORTED_PRICE_CURRENCIES:
        raise PricingError("Поддерживаются только RUB и XTR")
    return normalized


def _require_owner(db: Session, owner_user_id: int) -> None:
    if get_effective_access(db, owner_user_id).role != "owner":
        raise PricingError("Только владелец может менять цены")


def _default_profile_rows() -> list[tuple[str, str, int, str, int, int]]:
    rows: list[tuple[str, str, int, str, int, int]] = []
    for plan_code, durations in PRODUCTION_RUB_PRICES.items():
        for duration_months, (amount_minor, discount_percent) in durations.items():
            rows.append(
                (
                    "production",
                    plan_code,
                    duration_months,
                    "RUB",
                    amount_minor,
                    discount_percent,
                )
            )
            rows.append(
                (
                    "production",
                    plan_code,
                    duration_months,
                    "XTR",
                    PRODUCTION_XTR_PRICES[plan_code][duration_months],
                    discount_percent,
                )
            )
            # Minimum test prices. XTR uses whole Stars; RUB uses kopecks.
            rows.append(("test", plan_code, duration_months, "RUB", 100, 0))
            rows.append(("test", plan_code, duration_months, "XTR", 1, 0))
    return rows


def _settings_row(db: Session) -> PricingSettings | None:
    return db.get(PricingSettings, 1)


def get_pricing_state(db: Session) -> PricingState:
    settings = _settings_row(db)
    if settings is None:
        settings = seed_pricing_profiles(db)
    return PricingState(
        active_profile=settings.active_profile,
        updated_by_user_id=settings.updated_by_user_id,
        updated_at=settings.updated_at,
    )


def seed_pricing_profiles(
    db: Session,
    *,
    commit: bool = True,
) -> PricingSettings:
    """Create editable price presets without overwriting later owner edits."""

    settings = _settings_row(db)
    if settings is None:
        settings = PricingSettings(id=1, active_profile="production")
        db.add(settings)
        db.flush()
    elif settings.active_profile not in PRICING_PROFILES:
        settings.active_profile = "production"

    plans = {
        plan.code: plan
        for plan in db.scalars(
            select(Plan).where(Plan.code.in_(tuple(PRODUCTION_RUB_PRICES)))
        ).all()
    }
    missing = sorted(set(PRODUCTION_RUB_PRICES) - set(plans))
    if missing:
        raise PricingError(
            "Сначала создайте тарифы: " + ", ".join(missing)
        )

    for (
        profile_code,
        plan_code,
        duration_months,
        currency,
        amount_minor,
        discount_percent,
    ) in _default_profile_rows():
        plan = plans[plan_code]
        row = db.scalar(
            select(PricingProfilePrice).where(
                PricingProfilePrice.profile_code == profile_code,
                PricingProfilePrice.plan_id == plan.id,
                PricingProfilePrice.duration_months == duration_months,
                PricingProfilePrice.currency == currency,
            )
        )
        if row is None:
            db.add(
                PricingProfilePrice(
                    profile_code=profile_code,
                    plan_id=plan.id,
                    duration_months=duration_months,
                    currency=currency,
                    amount_minor=amount_minor,
                    discount_percent=discount_percent,
                    is_active=True,
                )
            )

    db.flush()
    sync_active_rub_prices(db, settings=settings, commit=False)

    if commit:
        db.commit()
        db.refresh(settings)
    else:
        db.flush()
    return settings


def list_profile_prices(
    db: Session,
    *,
    profile_code: str | None = None,
) -> list[PricingProfilePrice]:
    normalized = (
        get_pricing_state(db).active_profile
        if profile_code is None
        else _normalize_profile(profile_code)
    )
    return db.scalars(
        select(PricingProfilePrice)
        .where(
            PricingProfilePrice.profile_code == normalized,
            PricingProfilePrice.is_active.is_(True),
        )
        .order_by(
            PricingProfilePrice.plan_id,
            PricingProfilePrice.duration_months,
            PricingProfilePrice.currency,
        )
    ).all()


def get_profile_price(
    db: Session,
    *,
    profile_code: str,
    plan_code: str,
    duration_months: int,
    currency: str,
) -> PricingProfilePrice | None:
    normalized_profile = _normalize_profile(profile_code)
    normalized_plan = _normalize_plan_code(plan_code)
    normalized_currency = _normalize_currency(currency)
    plan = db.scalar(select(Plan).where(Plan.code == normalized_plan))
    if plan is None:
        return None
    return db.scalar(
        select(PricingProfilePrice).where(
            PricingProfilePrice.profile_code == normalized_profile,
            PricingProfilePrice.plan_id == plan.id,
            PricingProfilePrice.duration_months == duration_months,
            PricingProfilePrice.currency == normalized_currency,
            PricingProfilePrice.is_active.is_(True),
        )
    )


def get_active_profile_price(
    db: Session,
    *,
    plan_code: str,
    duration_months: int,
    currency: str,
) -> PricingProfilePrice | None:
    state = get_pricing_state(db)
    return get_profile_price(
        db,
        profile_code=state.active_profile,
        plan_code=plan_code,
        duration_months=duration_months,
        currency=currency,
    )


def sync_active_rub_prices(
    db: Session,
    *,
    settings: PricingSettings | None = None,
    commit: bool = True,
) -> None:
    """Keep old catalog readers in sync with the selected RUB price preset."""

    settings = settings or _settings_row(db)
    if settings is None:
        raise PricingError("Настройки цен не созданы")

    rows = db.scalars(
        select(PricingProfilePrice).where(
            PricingProfilePrice.profile_code == settings.active_profile,
            PricingProfilePrice.currency == "RUB",
            PricingProfilePrice.is_active.is_(True),
        )
    ).all()
    for row in rows:
        if row.amount_minor % 100:
            raise PricingError("Цена RUB должна содержать целое число рублей")
        legacy = db.scalar(
            select(PlanPrice).where(
                PlanPrice.plan_id == row.plan_id,
                PlanPrice.duration_months == row.duration_months,
            )
        )
        if legacy is not None:
            legacy.price_rub = row.amount_minor // 100
            legacy.discount_percent = row.discount_percent
            legacy.is_active = True

    if commit:
        db.commit()
    else:
        db.flush()


def set_active_pricing_profile(
    db: Session,
    *,
    owner_user_id: int,
    profile_code: str,
    now: datetime | None = None,
) -> PricingState:
    _require_owner(db, owner_user_id)
    normalized = _normalize_profile(profile_code)
    settings = _settings_row(db) or seed_pricing_profiles(db, commit=False)
    settings.active_profile = normalized
    settings.updated_by_user_id = owner_user_id
    settings.updated_at = now or datetime.utcnow()
    sync_active_rub_prices(db, settings=settings, commit=False)
    db.commit()
    db.refresh(settings)
    return PricingState(
        active_profile=settings.active_profile,
        updated_by_user_id=settings.updated_by_user_id,
        updated_at=settings.updated_at,
    )


def set_profile_price(
    db: Session,
    *,
    owner_user_id: int,
    profile_code: str,
    plan_code: str,
    duration_months: int,
    currency: str,
    amount_minor: int,
    discount_percent: int | None = None,
    now: datetime | None = None,
) -> PricingProfilePrice:
    _require_owner(db, owner_user_id)
    normalized_profile = _normalize_profile(profile_code)
    normalized_plan = _normalize_plan_code(plan_code)
    normalized_currency = _normalize_currency(currency)
    if duration_months not in PAID_DURATION_MONTHS:
        raise PricingError("Срок должен быть 1, 3, 6 или 12 месяцев")
    if amount_minor <= 0:
        raise PricingError("Цена должна быть больше нуля")
    if normalized_currency == "RUB" and amount_minor % 100:
        raise PricingError("Цена RUB должна содержать целое число рублей")
    if discount_percent is not None and not 0 <= discount_percent <= 99:
        raise PricingError("Скидка должна быть от 0 до 99 процентов")

    plan = db.scalar(
        select(Plan).where(
            Plan.code == normalized_plan,
            Plan.is_active.is_(True),
        )
    )
    if plan is None or plan.code == "trial":
        raise PricingError("Платный тариф не найден или отключён")

    row = db.scalar(
        select(PricingProfilePrice).where(
            PricingProfilePrice.profile_code == normalized_profile,
            PricingProfilePrice.plan_id == plan.id,
            PricingProfilePrice.duration_months == duration_months,
            PricingProfilePrice.currency == normalized_currency,
        )
    )
    if row is None:
        row = PricingProfilePrice(
            profile_code=normalized_profile,
            plan_id=plan.id,
            duration_months=duration_months,
            currency=normalized_currency,
            amount_minor=amount_minor,
            discount_percent=discount_percent or 0,
            is_active=True,
        )
        db.add(row)
    else:
        row.amount_minor = amount_minor
        if discount_percent is not None:
            row.discount_percent = discount_percent
        row.is_active = True
        row.updated_at = now or datetime.utcnow()

    settings = _settings_row(db) or seed_pricing_profiles(db, commit=False)
    settings.updated_by_user_id = owner_user_id
    settings.updated_at = now or datetime.utcnow()
    db.flush()
    if settings.active_profile == normalized_profile and normalized_currency == "RUB":
        sync_active_rub_prices(db, settings=settings, commit=False)
    db.commit()
    db.refresh(row)
    return row


def format_owner_pricing_text(db: Session) -> str:
    state = get_pricing_state(db)
    rows = list_profile_prices(db, profile_code=state.active_profile)
    plans = {
        plan.id: plan
        for plan in db.scalars(select(Plan).where(Plan.code.in_(("standard", "pro")))).all()
    }
    grouped: dict[str, dict[int, list[str]]] = {"standard": {}, "pro": {}}
    for row in rows:
        plan = plans.get(row.plan_id)
        if plan is None:
            continue
        if row.currency == "RUB":
            amount = f"{row.amount_minor // 100:,}".replace(",", " ") + " ₽"
        else:
            amount = f"{row.amount_minor} Stars"
        grouped[plan.code].setdefault(row.duration_months, []).append(amount)

    mode = "🧪 Тестовые" if state.active_profile == "test" else "💳 Рабочие"
    lines = [
        "👑 <b>Управление ценами</b>",
        "",
        f"Текущий режим: <b>{mode}</b>",
    ]
    if state.active_profile == "test":
        lines.extend(
            [
                "⚠️ Минимальные цены доступны всем новым покупателям.",
                "После проверки верните рабочий режим.",
            ]
        )
    for plan_code, title in (("standard", "Стандарт"), ("pro", "Pro")):
        lines.extend(["", f"<b>{title}</b>"])
        for duration in PAID_DURATION_MONTHS:
            values = grouped[plan_code].get(duration)
            if values:
                lines.append(f"{duration} мес. — {' / '.join(values)}")
    lines.extend(
        [
            "",
            "Кнопки меняют набор цен без перезапуска бота.",
        ]
    )
    return "\n".join(lines)
