from __future__ import annotations

import argparse
from datetime import datetime

from app.database import SessionLocal, init_db
from app.services.access_service import (
    AccessError,
    find_user_by_identity,
    get_effective_access,
    grant_admin_access,
    grant_beta_access,
    revoke_access,
)
from app.services.subscription_service import register_identity


def add_identity_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--platform",
        required=True,
        choices=("telegram", "discord"),
    )
    parser.add_argument("--external-id", required=True)
    parser.add_argument("--username")
    parser.add_argument("--first-name")
    parser.add_argument("--last-name")


def duration_from_args(args: argparse.Namespace, *, default_days: int | None) -> int | None:
    if getattr(args, "unlimited", False):
        return None
    days = getattr(args, "days", None)
    return default_days if days is None else days


def format_end(value: datetime | None) -> str:
    return "бессрочно" if value is None else value.strftime("%d.%m.%Y %H:%M")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Управление ролями Admin и Beta Tester",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    beta = subparsers.add_parser("grant-beta", help="Выдать Beta Tester")
    add_identity_args(beta)
    beta.add_argument("--days", type=int, help="Срок в днях; по умолчанию 30")
    beta.add_argument("--unlimited", action="store_true", help="Без даты окончания")
    beta.add_argument("--reason", default="Закрытый бета-тест")

    admin = subparsers.add_parser("grant-admin", help="Выдать Admin")
    add_identity_args(admin)
    admin.add_argument("--days", type=int, help="Срок в днях")
    admin.add_argument("--unlimited", action="store_true", help="Без даты окончания")
    admin.add_argument("--reason", default="Административный доступ")

    revoke = subparsers.add_parser("revoke", help="Отозвать роль")
    add_identity_args(revoke)
    revoke.add_argument(
        "--role",
        choices=("beta_tester", "admin"),
        help="Не указывать, чтобы отозвать все повышенные роли",
    )
    revoke.add_argument("--reason", default="Доступ отозван владельцем")

    show = subparsers.add_parser("show", help="Показать текущую роль")
    add_identity_args(show)

    args = parser.parse_args()
    init_db()
    db = SessionLocal()
    try:
        user = find_user_by_identity(
            db,
            platform=args.platform,
            external_user_id=args.external_id,
        )

        if args.command in {"grant-beta", "grant-admin"}:
            if user is None:
                user = register_identity(
                    db,
                    platform=args.platform,
                    external_user_id=args.external_id,
                    username=args.username,
                    first_name=args.first_name,
                    last_name=args.last_name,
                )

            if args.command == "grant-beta":
                duration_days = duration_from_args(args, default_days=30)
                grant = grant_beta_access(
                    db,
                    user_id=user.id,
                    duration_days=duration_days,
                    reason=args.reason,
                )
            else:
                duration_days = duration_from_args(args, default_days=None)
                grant = grant_admin_access(
                    db,
                    user_id=user.id,
                    duration_days=duration_days,
                    reason=args.reason,
                )

            print("Доступ выдан")
            print("Пользователь ID:", user.id)
            print("Платформа:", args.platform)
            print("Внешний ID:", args.external_id)
            print("Роль:", grant.role)
            print("Действует до:", format_end(grant.ends_at))
            return 0

        if user is None:
            print("Пользователь не найден")
            return 2

        if args.command == "revoke":
            count = revoke_access(
                db,
                user_id=user.id,
                role=args.role,
                reason=args.reason,
            )
            print("Отозвано активных доступов:", count)
            return 0

        state = get_effective_access(db, user.id)
        print("Пользователь ID:", user.id)
        print("Роль:", state.role)
        print("Безлимит:", "да" if state.unlimited else "нет")
        print("Действует до:", format_end(state.ends_at))
        return 0
    except AccessError as exc:
        print("Ошибка:", exc)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
