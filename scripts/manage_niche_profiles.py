from __future__ import annotations

import argparse
import json

from app.database import SessionLocal, init_db
from app.services.niche_profile_service import (
    get_questionnaire,
    list_niche_profiles,
    seed_niche_profiles,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Управление универсальными нишевыми профилями."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed", help="Создать или обновить профили ниш.")
    sub.add_parser("list", help="Показать все профили.")

    show = sub.add_parser("show", help="Показать анкету профиля.")
    show.add_argument("profile_code")

    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        if args.command == "seed":
            result = seed_niche_profiles(db)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        seed_niche_profiles(db)

        if args.command == "list":
            for profile in list_niche_profiles(db):
                marker = " [custom]" if profile.is_custom else ""
                print(f"{profile.code}: {profile.name}{marker}")
            return

        if args.command == "show":
            questions = get_questionnaire(db, args.profile_code)
            print(json.dumps(questions, ensure_ascii=False, indent=2))
            return
    finally:
        db.close()


if __name__ == "__main__":
    main()
