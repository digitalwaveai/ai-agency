from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AccessGrant, User, UserIdentity


ELEVATED_ROLES = {"admin", "beta_tester"}
ROLE_PRIORITY = {"customer": 0, "beta_tester": 10, "admin": 20}


class AccessError(ValueError):
    pass


@dataclass(frozen=True)
class AccessState:
    role: str
    unlimited: bool
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    grant_id: int | None = None
    source: str = "default"


def find_user_by_identity(
    db: Session,
    *,
    platform: str,
    external_user_id: str | int,
) -> User | None:
    identity = db.scalar(
        select(UserIdentity).where(
            UserIdentity.platform == platform.strip().lower(),
            UserIdentity.external_user_id == str(external_user_id).strip(),
        )
    )
    return db.get(User, identity.user_id) if identity is not None else None


def expire_access_grants(
    db: Session,
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> int:
    now = now or datetime.utcnow()
    grants = db.scalars(
        select(AccessGrant).where(
            AccessGrant.status == "active",
            AccessGrant.ends_at.is_not(None),
            AccessGrant.ends_at <= now,
        )
    ).all()
    for grant in grants:
        grant.status = "expired"
        grant.updated_at = now
    if grants and commit:
        db.commit()
    elif grants:
        db.flush()
    return len(grants)


def get_effective_access(
    db: Session,
    user_id: int,
    *,
    now: datetime | None = None,
) -> AccessState:
    now = now or datetime.utcnow()
    user = db.get(User, user_id)
    if user is None:
        raise AccessError("Пользователь не найден")

    if user.is_admin:
        return AccessState(
            role="admin",
            unlimited=True,
            source="legacy_admin_flag",
        )

    expire_access_grants(db, now=now)
    grants = db.scalars(
        select(AccessGrant).where(
            AccessGrant.user_id == user_id,
            AccessGrant.status == "active",
            AccessGrant.starts_at <= now,
            (AccessGrant.ends_at.is_(None) | (AccessGrant.ends_at > now)),
        )
    ).all()
    if not grants:
        return AccessState(role="customer", unlimited=False)

    grant = max(
        grants,
        key=lambda item: (
            ROLE_PRIORITY.get(item.role, -1),
            item.created_at,
            item.id,
        ),
    )
    return AccessState(
        role=grant.role,
        unlimited=grant.role in ELEVATED_ROLES,
        starts_at=grant.starts_at,
        ends_at=grant.ends_at,
        grant_id=grant.id,
        source="access_grant",
    )


def grant_access(
    db: Session,
    *,
    user_id: int,
    role: str,
    duration_days: int | None,
    granted_by_user_id: int | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> AccessGrant:
    now = now or datetime.utcnow()
    role = role.strip().lower()
    if role not in ELEVATED_ROLES:
        raise AccessError("Разрешены только роли admin и beta_tester")
    if duration_days is not None and not 1 <= duration_days <= 3650:
        raise AccessError("Срок должен быть от 1 до 3650 дней или бессрочно")
    if db.get(User, user_id) is None:
        raise AccessError("Пользователь не найден")
    if granted_by_user_id is not None and db.get(User, granted_by_user_id) is None:
        raise AccessError("Администратор, выдавший доступ, не найден")

    current_admin = get_effective_access(db, user_id, now=now)
    if current_admin.role == "admin" and role == "beta_tester":
        raise AccessError("Нельзя заменить действующий admin-доступ на beta_tester")

    active_same_role = db.scalars(
        select(AccessGrant).where(
            AccessGrant.user_id == user_id,
            AccessGrant.role == role,
            AccessGrant.status == "active",
        )
    ).all()
    for grant in active_same_role:
        grant.status = "superseded"
        grant.revoked_at = now
        grant.revoked_by_user_id = granted_by_user_id
        grant.updated_at = now

    ends_at = None if duration_days is None else now + timedelta(days=duration_days)
    grant = AccessGrant(
        user_id=user_id,
        role=role,
        status="active",
        starts_at=now,
        ends_at=ends_at,
        granted_by_user_id=granted_by_user_id,
        reason=reason,
        created_at=now,
        updated_at=now,
    )
    db.add(grant)
    db.commit()
    db.refresh(grant)
    return grant


def grant_beta_access(
    db: Session,
    *,
    user_id: int,
    duration_days: int | None = 30,
    granted_by_user_id: int | None = None,
    reason: str | None = "Закрытый бета-тест",
    now: datetime | None = None,
) -> AccessGrant:
    return grant_access(
        db,
        user_id=user_id,
        role="beta_tester",
        duration_days=duration_days,
        granted_by_user_id=granted_by_user_id,
        reason=reason,
        now=now,
    )


def grant_admin_access(
    db: Session,
    *,
    user_id: int,
    duration_days: int | None = None,
    granted_by_user_id: int | None = None,
    reason: str | None = "Административный доступ",
    now: datetime | None = None,
) -> AccessGrant:
    return grant_access(
        db,
        user_id=user_id,
        role="admin",
        duration_days=duration_days,
        granted_by_user_id=granted_by_user_id,
        reason=reason,
        now=now,
    )


def revoke_access(
    db: Session,
    *,
    user_id: int,
    role: str | None = None,
    revoked_by_user_id: int | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> int:
    now = now or datetime.utcnow()
    query = select(AccessGrant).where(
        AccessGrant.user_id == user_id,
        AccessGrant.status == "active",
    )
    if role is not None:
        normalized_role = role.strip().lower()
        if normalized_role not in ELEVATED_ROLES:
            raise AccessError("Неизвестная роль")
        query = query.where(AccessGrant.role == normalized_role)

    grants = db.scalars(query).all()
    for grant in grants:
        grant.status = "revoked"
        grant.revoked_at = now
        grant.revoked_by_user_id = revoked_by_user_id
        if reason:
            grant.reason = f"{grant.reason or ''}\nОтзыв: {reason}".strip()
        grant.updated_at = now
    if grants:
        db.commit()
    return len(grants)
