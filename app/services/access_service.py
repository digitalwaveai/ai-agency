from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AccessGrant, User, UserIdentity


ASSIGNABLE_ROLES = {"admin", "beta_tester"}
UNLIMITED_ROLES = {"owner", "admin", "beta_tester"}
ROLE_PRIORITY = {"customer": 0, "beta_tester": 10, "admin": 20, "owner": 30}


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
            AccessGrant.role != "owner",
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

    expire_access_grants(db, now=now)
    grants = db.scalars(
        select(AccessGrant).where(
            AccessGrant.user_id == user_id,
            AccessGrant.status == "active",
            AccessGrant.starts_at <= now,
            (AccessGrant.ends_at.is_(None) | (AccessGrant.ends_at > now)),
        )
    ).all()
    if grants:
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
            unlimited=grant.role in UNLIMITED_ROLES,
            starts_at=grant.starts_at,
            ends_at=grant.ends_at,
            grant_id=grant.id,
            source="access_grant",
        )

    if user.is_admin:
        return AccessState(
            role="admin",
            unlimited=True,
            source="legacy_admin_flag",
        )

    return AccessState(role="customer", unlimited=False)


def ensure_owner_access(
    db: Session,
    *,
    user_id: int,
    reason: str | None = "Владелец LeadPilot AI",
    now: datetime | None = None,
) -> AccessGrant:
    """Make exactly one configured user the permanent owner.

    Owner is above admin, has unlimited access and cannot be revoked through
    normal admin actions. Calling this for a new owner supersedes previous
    owner grants, which also makes OWNER_TELEGRAM_ID rotation safe.
    """

    now = now or datetime.utcnow()
    user = db.get(User, user_id)
    if user is None:
        raise AccessError("Пользователь не найден")

    active_owners = db.scalars(
        select(AccessGrant).where(
            AccessGrant.role == "owner",
            AccessGrant.status == "active",
        )
    ).all()
    current: AccessGrant | None = None
    for grant in active_owners:
        if grant.user_id == user_id:
            current = grant
            grant.ends_at = None
            grant.updated_at = now
        else:
            grant.status = "superseded"
            grant.revoked_at = now
            grant.revoked_by_user_id = user_id
            grant.updated_at = now
            previous_owner = db.get(User, grant.user_id)
            if previous_owner is not None:
                previous_owner.is_admin = False

    if current is None:
        current = AccessGrant(
            user_id=user_id,
            role="owner",
            status="active",
            starts_at=now,
            ends_at=None,
            granted_by_user_id=user_id,
            reason=reason,
            created_at=now,
            updated_at=now,
        )
        db.add(current)

    user.is_admin = True
    db.commit()
    db.refresh(current)
    return current


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
    if role not in ASSIGNABLE_ROLES:
        raise AccessError("Разрешены только роли admin и beta_tester")
    if duration_days is not None and not 1 <= duration_days <= 3650:
        raise AccessError("Срок должен быть от 1 до 3650 дней или бессрочно")
    if db.get(User, user_id) is None:
        raise AccessError("Пользователь не найден")
    if granted_by_user_id is not None and db.get(User, granted_by_user_id) is None:
        raise AccessError("Пользователь, выдавший доступ, не найден")

    current_access = get_effective_access(db, user_id, now=now)
    if current_access.role == "owner":
        raise AccessError("Нельзя изменить роль владельца")
    if current_access.role == "admin" and role == "beta_tester":
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


def grant_admin_by_owner(
    db: Session,
    *,
    owner_user_id: int,
    target_user_id: int,
    duration_days: int | None = None,
    reason: str | None = "Выдано владельцем",
    now: datetime | None = None,
) -> AccessGrant:
    owner = get_effective_access(db, owner_user_id, now=now)
    if owner.role != "owner":
        raise AccessError("Только владелец может выдавать роль admin")
    if owner_user_id == target_user_id:
        raise AccessError("Владелец уже имеет максимальный доступ")
    return grant_admin_access(
        db,
        user_id=target_user_id,
        duration_days=duration_days,
        granted_by_user_id=owner_user_id,
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
    if get_effective_access(db, user_id, now=now).role == "owner":
        raise AccessError("Роль владельца нельзя отозвать")

    query = select(AccessGrant).where(
        AccessGrant.user_id == user_id,
        AccessGrant.status == "active",
        AccessGrant.role.in_(ASSIGNABLE_ROLES),
    )
    if role is not None:
        normalized_role = role.strip().lower()
        if normalized_role not in ASSIGNABLE_ROLES:
            raise AccessError("Неизвестная роль")
        query = query.where(AccessGrant.role == normalized_role)

    grants = db.scalars(query).all()
    legacy_admin_revoked = 0
    user = db.get(User, user_id)
    if role in {None, "admin"} and user is not None and user.is_admin:
        user.is_admin = False
        legacy_admin_revoked = 1

    for grant in grants:
        grant.status = "revoked"
        grant.revoked_at = now
        grant.revoked_by_user_id = revoked_by_user_id
        if reason:
            grant.reason = f"{grant.reason or ''}\nОтзыв: {reason}".strip()
        grant.updated_at = now
    if grants or legacy_admin_revoked:
        db.commit()
    return len(grants) + legacy_admin_revoked


def revoke_admin_by_owner(
    db: Session,
    *,
    owner_user_id: int,
    target_user_id: int,
    reason: str | None = "Отозвано владельцем",
    now: datetime | None = None,
) -> int:
    owner = get_effective_access(db, owner_user_id, now=now)
    if owner.role != "owner":
        raise AccessError("Только владелец может отзывать роль admin")
    if owner_user_id == target_user_id:
        raise AccessError("Роль владельца нельзя отозвать")
    return revoke_access(
        db,
        user_id=target_user_id,
        role="admin",
        revoked_by_user_id=owner_user_id,
        reason=reason,
        now=now,
    )
