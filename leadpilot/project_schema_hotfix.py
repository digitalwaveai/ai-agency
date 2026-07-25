from __future__ import annotations

import threading
from functools import wraps
from typing import Any

_SCHEMA_LOCK = threading.RLock()


def _project_columns(db: Any) -> set[str]:
    with db._connect() as connection:
        if db.is_postgres:
            rows = connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'projects'
                """
            ).fetchall()
            return {str(row["column_name"]) for row in rows}
        return {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(projects)").fetchall()
        }


def _ensure_project_columns(db: Any) -> None:
    """Run the questionnaire migration only during startup or project writes."""
    with _SCHEMA_LOCK:
        columns = _project_columns(db)
        missing_priorities = "priorities" not in columns
        missing_exclusions = "exclusions" not in columns
        if not missing_priorities and not missing_exclusions:
            return
        with db._connect() as connection:
            if missing_priorities:
                connection.execute(
                    "ALTER TABLE projects ADD COLUMN priorities "
                    "TEXT NOT NULL DEFAULT ''"
                )
            if missing_exclusions:
                connection.execute(
                    "ALTER TABLE projects ADD COLUMN exclusions "
                    "TEXT NOT NULL DEFAULT ''"
                )
            connection.commit()


def _select_parts(db: Any) -> tuple[str, str]:
    columns = _project_columns(db)
    priorities = "priorities" if "priorities" in columns else "'' AS priorities"
    exclusions = "exclusions" if "exclusions" in columns else "'' AS exclusions"
    return priorities, exclusions


def install_project_schema_hotfix(database_class: type[Any]) -> None:
    """Read projects safely without running ALTER TABLE from Telegram handlers."""
    if getattr(database_class, "_project_schema_hotfix_installed", False):
        return

    old_init_schema = database_class.init_schema
    old_create_project = database_class.create_project

    @wraps(old_init_schema)
    def init_schema(self: Any) -> None:
        old_init_schema(self)
        _ensure_project_columns(self)

    @wraps(old_create_project)
    def create_project(self: Any, *args: Any, **kwargs: Any):
        _ensure_project_columns(self)
        return old_create_project(self, *args, **kwargs)

    def list_projects(
        self: Any, user_id: int, limit: int = 20
    ) -> list[dict[str, Any]]:
        priorities, exclusions = _select_parts(self)
        statement = self._sql(
            f"""
            SELECT id, name, category_code, category_name, niche, offer,
                   target_audience, region, advantage,
                   {priorities}, {exclusions}, status, created_at
            FROM projects
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """
        )
        with self._connect() as connection:
            rows = connection.execute(statement, (user_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def get_project(
        self: Any, user_id: int, project_id: int
    ) -> dict[str, Any] | None:
        priorities, exclusions = _select_parts(self)
        statement = self._sql(
            f"""
            SELECT id, name, category_code, category_name, niche, offer,
                   target_audience, region, advantage,
                   {priorities}, {exclusions}, status, created_at
            FROM projects
            WHERE user_id = ? AND id = ?
            """
        )
        with self._connect() as connection:
            row = connection.execute(statement, (user_id, project_id)).fetchone()
        return dict(row) if row else None

    database_class.init_schema = init_schema
    database_class.create_project = create_project
    database_class.list_projects = list_projects
    database_class.get_project = get_project
    database_class._project_schema_hotfix_installed = True
