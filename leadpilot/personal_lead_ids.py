from __future__ import annotations

import threading
from functools import wraps
from typing import Any

from .models import Lead


_LEAD_ID_LOCK = threading.RLock()


def _ensure_personal_id_schema(database: Any) -> None:
    with database._connect() as connection:
        if database.is_postgres:
            connection.execute(
                "ALTER TABLE leads ADD COLUMN IF NOT EXISTS user_lead_id BIGINT"
            )
        else:
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(leads)").fetchall()
            }
            if "user_lead_id" not in columns:
                connection.execute("ALTER TABLE leads ADD COLUMN user_lead_id BIGINT")

        rows = connection.execute(
            "SELECT id, user_id, user_lead_id FROM leads ORDER BY user_id, id"
        ).fetchall()
        next_by_user: dict[int, int] = {}
        for row in rows:
            user_id = int(row["user_id"])
            current = row["user_lead_id"]
            if current is not None:
                next_by_user[user_id] = max(
                    next_by_user.get(user_id, 0),
                    int(current),
                )

        update_statement = database._sql(
            "UPDATE leads SET user_lead_id = ? WHERE id = ?"
        )
        for row in rows:
            if row["user_lead_id"] is not None:
                continue
            user_id = int(row["user_id"])
            next_id = next_by_user.get(user_id, 0) + 1
            next_by_user[user_id] = next_id
            connection.execute(update_statement, (next_id, int(row["id"])))

        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_leads_user_personal_id ON leads (user_id, user_lead_id)"
        )
        connection.commit()


def install_personal_lead_ids(database_class: type[Any]) -> None:
    """Expose lead IDs as a separate 1..N sequence for every Telegram user."""
    if getattr(database_class, "_personal_lead_ids_installed", False):
        return

    original_init_schema = database_class.init_schema

    @wraps(original_init_schema)
    def init_schema(self: Any) -> None:
        original_init_schema(self)
        _ensure_personal_id_schema(self)

    def save_leads(
        self: Any,
        user_id: int,
        leads: list[Lead],
        project_id: int | None = None,
    ) -> list[int]:
        select_existing = self._sql(
            """
            SELECT user_lead_id
            FROM leads
            WHERE user_id = ? AND source_url = ?
            """
        )
        select_next = self._sql(
            """
            SELECT COALESCE(MAX(user_lead_id), 0) + 1 AS next_id
            FROM leads
            WHERE user_id = ?
            """
        )
        update_statement = self._sql(
            """
            UPDATE leads
            SET name = ?, website = ?, phone = ?, address = ?, snippet = ?,
                search_query = ?, score = ?,
                project_id = COALESCE(?, project_id)
            WHERE user_id = ? AND source_url = ?
            """
        )
        insert_statement = self._sql(
            """
            INSERT INTO leads (
                user_id, user_lead_id, name, source_url, website, phone,
                address, snippet, search_query, score, project_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        )

        ids: list[int] = []
        with _LEAD_ID_LOCK, self._connect() as connection:
            for lead in leads:
                existing = connection.execute(
                    select_existing,
                    (user_id, lead.source_url),
                ).fetchone()
                if existing is not None:
                    personal_id = int(existing["user_lead_id"])
                    connection.execute(
                        update_statement,
                        (
                            lead.name,
                            lead.website,
                            lead.phone,
                            lead.address,
                            lead.snippet,
                            lead.query,
                            lead.score,
                            project_id,
                            user_id,
                            lead.source_url,
                        ),
                    )
                else:
                    row = connection.execute(select_next, (user_id,)).fetchone()
                    personal_id = int(row["next_id"])
                    connection.execute(
                        insert_statement,
                        (
                            user_id,
                            personal_id,
                            lead.name,
                            lead.source_url,
                            lead.website,
                            lead.phone,
                            lead.address,
                            lead.snippet,
                            lead.query,
                            lead.score,
                            project_id,
                        ),
                    )
                ids.append(personal_id)
            connection.commit()
        return ids

    def list_leads(self: Any, user_id: int, limit: int = 10) -> list[Lead]:
        statement = self._sql(
            """
            SELECT user_lead_id AS id, name, source_url, website, phone,
                   address, snippet, search_query, score, status
            FROM leads
            WHERE user_id = ?
            ORDER BY user_lead_id DESC
            LIMIT ?
            """
        )
        with self._connect() as connection:
            rows = connection.execute(statement, (user_id, limit)).fetchall()
        return [self._row_to_lead(row) for row in rows]

    def get_lead(self: Any, user_id: int, lead_id: int) -> Lead | None:
        statement = self._sql(
            """
            SELECT user_lead_id AS id, name, source_url, website, phone,
                   address, snippet, search_query, score, status
            FROM leads
            WHERE user_id = ? AND user_lead_id = ?
            """
        )
        with self._connect() as connection:
            row = connection.execute(statement, (user_id, lead_id)).fetchone()
        return self._row_to_lead(row) if row else None

    def update_lead_status(
        self: Any,
        user_id: int,
        lead_id: int,
        status: str,
    ) -> bool:
        statement = self._sql(
            """
            UPDATE leads
            SET status = ?
            WHERE user_id = ? AND user_lead_id = ?
            """
        )
        with self._connect() as connection:
            cursor = connection.execute(statement, (status, user_id, lead_id))
            changed = cursor.rowcount > 0
            connection.commit()
        return changed

    database_class.init_schema = init_schema
    database_class.save_leads = save_leads
    database_class.list_leads = list_leads
    database_class.get_lead = get_lead
    database_class.update_lead_status = update_lead_status
    database_class._personal_lead_ids_installed = True
