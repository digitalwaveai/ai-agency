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
            raise RuntimeError(
                "DATABASE_URL должен начинаться с postgresql:// или sqlite:///"
            )
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
        id_column = (
            "BIGSERIAL PRIMARY KEY"
            if self.is_postgres
            else "INTEGER PRIMARY KEY AUTOINCREMENT"
        )
        leads_statement = f"""
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
                status TEXT NOT NULL DEFAULT 'new',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, source_url)
            )
        """
        projects_statement = f"""
            CREATE TABLE IF NOT EXISTS projects (
                id {id_column},
                user_id BIGINT NOT NULL,
                name TEXT NOT NULL,
                niche TEXT NOT NULL,
                region TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """
        radars_statement = f"""
            CREATE TABLE IF NOT EXISTS radars (
                id {id_column},
                user_id BIGINT NOT NULL,
                niches TEXT NOT NULL,
                regions TEXT NOT NULL,
                result_limit INTEGER NOT NULL DEFAULT 3,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """
        with self._connect() as connection:
            connection.execute(leads_statement)
            connection.execute(projects_statement)
            connection.execute(radars_statement)
            if self.is_postgres:
                connection.execute(
                    "ALTER TABLE leads "
                    "ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'new'"
                )
            else:
                columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(leads)").fetchall()
                }
                if "status" not in columns:
                    connection.execute(
                        "ALTER TABLE leads "
                        "ADD COLUMN status TEXT NOT NULL DEFAULT 'new'"
                    )
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
                ids.append(
                    int(row["id"] if isinstance(row, sqlite3.Row) else row["id"])
                )
            connection.commit()
        return ids

    def list_leads(self, user_id: int, limit: int = 10) -> list[Lead]:
        statement = self._sql(
            """
            SELECT id, name, source_url, website, phone, address,
                   snippet, search_query, score, status
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
                   snippet, search_query, score, status
            FROM leads
            WHERE user_id = ? AND id = ?
            """
        )
        with self._connect() as connection:
            row = connection.execute(statement, (user_id, lead_id)).fetchone()
        return self._row_to_lead(row) if row else None

    def update_lead_status(
        self, user_id: int, lead_id: int, status: str
    ) -> bool:
        statement = self._sql(
            """
            UPDATE leads
            SET status = ?
            WHERE user_id = ? AND id = ?
            """
        )
        with self._connect() as connection:
            cursor = connection.execute(statement, (status, user_id, lead_id))
            changed = cursor.rowcount > 0
            connection.commit()
        return changed

    def create_project(
        self, user_id: int, name: str, niche: str, region: str
    ) -> int:
        statement = self._sql(
            """
            INSERT INTO projects (user_id, name, niche, region)
            VALUES (?, ?, ?, ?)
            RETURNING id
            """
        )
        with self._connect() as connection:
            row = connection.execute(
                statement, (user_id, name, niche, region)
            ).fetchone()
            connection.commit()
        return int(row["id"])

    def list_projects(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        statement = self._sql(
            """
            SELECT id, name, niche, region, created_at
            FROM projects
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """
        )
        with self._connect() as connection:
            rows = connection.execute(statement, (user_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def create_radar(
        self,
        user_id: int,
        niches: list[str],
        regions: list[str],
        result_limit: int,
    ) -> int:
        statement = self._sql(
            """
            INSERT INTO radars (user_id, niches, regions, result_limit)
            VALUES (?, ?, ?, ?)
            RETURNING id
            """
        )
        with self._connect() as connection:
            row = connection.execute(
                statement,
                (user_id, "\n".join(niches), "\n".join(regions), result_limit),
            ).fetchone()
            connection.commit()
        return int(row["id"])

    def list_radars(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        statement = self._sql(
            """
            SELECT id, niches, regions, result_limit, active, created_at
            FROM radars
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """
        )
        with self._connect() as connection:
            rows = connection.execute(statement, (user_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def get_radar(self, user_id: int, radar_id: int) -> dict[str, Any] | None:
        statement = self._sql(
            """
            SELECT id, niches, regions, result_limit, active, created_at
            FROM radars
            WHERE user_id = ? AND id = ?
            """
        )
        with self._connect() as connection:
            row = connection.execute(statement, (user_id, radar_id)).fetchone()
        return dict(row) if row else None

    def lead_statistics(self, user_id: int) -> dict[str, int]:
        statement = self._sql(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(ROUND(AVG(score)), 0) AS average_score,
                COALESCE(SUM(CASE WHEN score >= 80 THEN 1 ELSE 0 END), 0)
                    AS high_score,
                COALESCE(SUM(CASE
                    WHEN phone <> '' OR website <> '' THEN 1 ELSE 0 END), 0)
                    AS with_contacts,
                COALESCE(SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END), 0)
                    AS new_count,
                COALESCE(SUM(CASE WHEN status = 'contacted' THEN 1 ELSE 0 END), 0)
                    AS contacted_count,
                COALESCE(SUM(CASE WHEN status = 'replied' THEN 1 ELSE 0 END), 0)
                    AS replied_count
            FROM leads
            WHERE user_id = ?
            """
        )
        with self._connect() as connection:
            row = connection.execute(statement, (user_id,)).fetchone()
        return {key: int(value or 0) for key, value in dict(row).items()}

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
            status=data.get("status", "new"),
        )
