from __future__ import annotations

import threading
from functools import wraps
from typing import Any

_SCHEMA_LOCK = threading.RLock()


def _ensure_project_columns(db: Any) -> None:
    """Ensure questionnaire columns exist before project reads or writes."""
    with _SCHEMA_LOCK:
        with db._connect() as connection:
            if db.is_postgres:
                connection.execute(
                    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS "
                    "priorities TEXT NOT NULL DEFAULT ''"
                )
                connection.execute(
                    "ALTER TABLE projects ADD COLUMN IF NOT EXISTS "
                    "exclusions TEXT NOT NULL DEFAULT ''"
                )
            else:
                columns = {
                    str(row["name"])
                    for row in connection.execute(
                        "PRAGMA table_info(projects)"
                    ).fetchall()
                }
                if "priorities" not in columns:
                    connection.execute(
                        "ALTER TABLE projects ADD COLUMN priorities "
                        "TEXT NOT NULL DEFAULT ''"
                    )
                if "exclusions" not in columns:
                    connection.execute(
                        "ALTER TABLE projects ADD COLUMN exclusions "
                        "TEXT NOT NULL DEFAULT ''"
                    )
            connection.commit()


def install_project_schema_hotfix(database_class: type[Any]) -> None:
    """Make project buttons resilient to a missed production migration."""
    if getattr(database_class, "_project_schema_hotfix_installed", False):
        return

    old_init_schema = database_class.init_schema
    old_create_project = database_class.create_project
    old_list_projects = database_class.list_projects
    old_get_project = database_class.get_project

    @wraps(old_init_schema)
    def init_schema(self: Any) -> None:
        old_init_schema(self)
        _ensure_project_columns(self)

    @wraps(old_create_project)
    def create_project(self: Any, *args: Any, **kwargs: Any):
        _ensure_project_columns(self)
        return old_create_project(self, *args, **kwargs)

    @wraps(old_list_projects)
    def list_projects(self: Any, *args: Any, **kwargs: Any):
        _ensure_project_columns(self)
        return old_list_projects(self, *args, **kwargs)

    @wraps(old_get_project)
    def get_project(self: Any, *args: Any, **kwargs: Any):
        _ensure_project_columns(self)
        return old_get_project(self, *args, **kwargs)

    database_class.init_schema = init_schema
    database_class.create_project = create_project
    database_class.list_projects = list_projects
    database_class.get_project = get_project
    database_class._project_schema_hotfix_installed = True
