from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .models import Lead


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.is_postgres = url.startswith(("postgres://", "postgresql://"))

    def _connect(self):
        if self.is_postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError(
                    "Для PostgreSQL установите зависимости из requirements.txt"
                ) from exc
            return psycopg.connect(self.url, row_factory=dict_row)

        prefix = "sqlite:///"
        if not self.url.startswith(prefix):
            raise RuntimeError("DATABASE_URL должен начинаться с postgresql:// или sqlite:///")
        db_path = self.url[len(prefix) :]
        if db_path != ":memory:":
            path = Path(db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _sql(self, statement: str) -> str:
        return statement.replace("?", "%s") if self.is_postgres else statement

    def init_schema(self) -> None:
        id_column = "BIGSERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        statement = f"""
            CREATE TABLE IF NOT EXISTS leads (
                id {id_column},
                user_id BIGINT NOT NULL,
                name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                website TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                snippet TEXT NOT NULL DEFAULT '',
                search_query TEXT NOT NULL DEFAULT '',
                score INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, source_url)
            )
        """
        with self._connect() as connection:
            connection.execute(statement)
            connection.commit()

    def save_leads(self, user_id: int, leads: list[Lead]) -> list[int]:
        statement = self._sql(
            """
            INSERT INTO leads (
                user_id, name, source_url, website, phone, address,
                snippet, search_query, score
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, source_url) DO UPDATE SET
                name = excluded.name,
                website = excluded.website,
                phone = excluded.phone,
                address = excluded.address,
                snippet = excluded.snippet,
                search_query = excluded.search_query,
                score = excluded.score
            RETURNING id
            """
        )
        ids: list[int] = []
        with self._connect() as connection:
            for lead in leads:
                row = connection.execute(
                    statement,
                    (
                        user_id,
                        lead.name,
                        lead.source_url,
                        lead.website,
                        lead.phone,
                        lead.address,
                        lead.snippet,
                        lead.query,
                        lead.score,
                    ),
                ).fetchone()
                ids.append(int(row["id"] if isinstance(row, sqlite3.Row) else row["id"]))
            connection.commit()
        return ids

    def list_leads(self, user_id: int, limit: int = 10) -> list[Lead]:
        statement = self._sql(
            """
            SELECT id, name, source_url, website, phone, address,
                   snippet, search_query, score
            FROM leads
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """
        )
        with self._connect() as connection:
            rows = connection.execute(statement, (user_id, limit)).fetchall()
        return [self._row_to_lead(row) for row in rows]

    def get_lead(self, user_id: int, lead_id: int) -> Lead | None:
        statement = self._sql(
            """
            SELECT id, name, source_url, website, phone, address,
                   snippet, search_query, score
            FROM leads
            WHERE user_id = ? AND id = ?
            """
        )
        with self._connect() as connection:
            row = connection.execute(statement, (user_id, lead_id)).fetchone()
        return self._row_to_lead(row) if row else None

    @staticmethod
    def _row_to_lead(row: Any) -> Lead:
        data = dict(row)
        return Lead(
            id=int(data["id"]),
            name=data["name"],
            source_url=data["source_url"],
            website=data["website"],
            phone=data["phone"],
            address=data["address"],
            snippet=data["snippet"],
            query=data["search_query"],
            score=int(data["score"]),
        )
