from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
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
                project_id BIGINT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, source_url)
            )
        """
        projects_statement = f"""
            CREATE TABLE IF NOT EXISTS projects (
                id {id_column},
                user_id BIGINT NOT NULL,
                name TEXT NOT NULL,
                category_code TEXT NOT NULL DEFAULT 'custom',
                category_name TEXT NOT NULL DEFAULT 'Своя ниша',
                niche TEXT NOT NULL,
                offer TEXT NOT NULL DEFAULT '',
                target_audience TEXT NOT NULL DEFAULT '',
                region TEXT NOT NULL DEFAULT '',
                advantage TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
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
        accounts_statement = """
            CREATE TABLE IF NOT EXISTS user_accounts (
                user_id BIGINT PRIMARY KEY,
                username TEXT NOT NULL DEFAULT '',
                first_name TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'user',
                managed_by BIGINT,
                price_mode TEXT NOT NULL DEFAULT 'live',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """
        payments_statement = """
            CREATE TABLE IF NOT EXISTS star_payments (
                telegram_payment_charge_id TEXT PRIMARY KEY,
                provider_payment_charge_id TEXT NOT NULL DEFAULT '',
                user_id BIGINT NOT NULL,
                plan_code TEXT NOT NULL,
                duration_months INTEGER NOT NULL,
                amount_stars INTEGER NOT NULL,
                paid_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """
        subscriptions_statement = f"""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id {id_column},
                user_id BIGINT NOT NULL,
                plan_code TEXT NOT NULL,
                starts_at TIMESTAMP NOT NULL,
                ends_at TIMESTAMP NOT NULL,
                source TEXT NOT NULL DEFAULT 'stars',
                payment_charge_id TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """
        with self._connect() as connection:
            connection.execute(leads_statement)
            connection.execute(projects_statement)
            connection.execute(radars_statement)
            connection.execute(accounts_statement)
            connection.execute(payments_statement)
            connection.execute(subscriptions_statement)
            if self.is_postgres:
                migrations = (
                    ("leads", "status", "TEXT NOT NULL DEFAULT 'new'"),
                    ("leads", "project_id", "BIGINT"),
                    ("projects", "category_code", "TEXT NOT NULL DEFAULT 'custom'"),
                    (
                        "projects",
                        "category_name",
                        "TEXT NOT NULL DEFAULT 'Своя ниша'",
                    ),
                    ("projects", "offer", "TEXT NOT NULL DEFAULT ''"),
                    ("projects", "target_audience", "TEXT NOT NULL DEFAULT ''"),
                    ("projects", "advantage", "TEXT NOT NULL DEFAULT ''"),
                    ("projects", "status", "TEXT NOT NULL DEFAULT 'active'"),
                    ("user_accounts", "role", "TEXT NOT NULL DEFAULT 'user'"),
                    ("user_accounts", "managed_by", "BIGINT"),
                    (
                        "user_accounts",
                        "price_mode",
                        "TEXT NOT NULL DEFAULT 'live'",
                    ),
                )
                for table, column, definition in migrations:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
                        f"{column} {definition}"
                    )
            else:
                migrations = {
                    "leads": (
                        ("status", "TEXT NOT NULL DEFAULT 'new'"),
                        ("project_id", "BIGINT"),
                    ),
                    "projects": (
                        ("category_code", "TEXT NOT NULL DEFAULT 'custom'"),
                        (
                            "category_name",
                            "TEXT NOT NULL DEFAULT 'Своя ниша'",
                        ),
                        ("offer", "TEXT NOT NULL DEFAULT ''"),
                        ("target_audience", "TEXT NOT NULL DEFAULT ''"),
                        ("advantage", "TEXT NOT NULL DEFAULT ''"),
                        ("status", "TEXT NOT NULL DEFAULT 'active'"),
                    ),
                    "user_accounts": (
                        ("role", "TEXT NOT NULL DEFAULT 'user'"),
                        ("managed_by", "BIGINT"),
                        ("price_mode", "TEXT NOT NULL DEFAULT 'live'"),
                    ),
                }
                for table, definitions in migrations.items():
                    columns = {
                        str(row["name"])
                        for row in connection.execute(
                            f"PRAGMA table_info({table})"
                        ).fetchall()
                    }
                    for column, definition in definitions:
                        if column not in columns:
                            connection.execute(
                                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                            )
            connection.commit()

    def ensure_account(
        self,
        user_id: int,
        *,
        username: str = "",
        first_name: str = "",
    ) -> None:
        statement = self._sql(
            """
            INSERT INTO user_accounts (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                updated_at = CURRENT_TIMESTAMP
            """
        )
        with self._connect() as connection:
            connection.execute(statement, (user_id, username, first_name))
            connection.commit()

    def ensure_owner(self, owner_user_id: int) -> None:
        """Keep exactly one permanent owner, identified by Railway settings."""
        demote_statement = self._sql(
            """
            UPDATE user_accounts
            SET role = 'user', managed_by = NULL, price_mode = 'live',
                updated_at = CURRENT_TIMESTAMP
            WHERE role = 'owner' AND user_id <> ?
            """
        )
        owner_statement = self._sql(
            """
            INSERT INTO user_accounts (user_id, role)
            VALUES (?, 'owner')
            ON CONFLICT(user_id) DO UPDATE SET
                role = 'owner',
                managed_by = NULL,
                updated_at = CURRENT_TIMESTAMP
            """
        )
        with self._connect() as connection:
            connection.execute(demote_statement, (owner_user_id,))
            connection.execute(owner_statement, (owner_user_id,))
            connection.commit()

    def get_role(self, user_id: int) -> str:
        statement = self._sql("SELECT role FROM user_accounts WHERE user_id = ?")
        with self._connect() as connection:
            row = connection.execute(statement, (user_id,)).fetchone()
        role = str(row["role"]) if row else "user"
        return role if role in {"owner", "admin", "beta_tester", "user"} else "user"

    def get_role_record(self, user_id: int) -> dict[str, Any]:
        statement = self._sql(
            """
            SELECT user_id, username, first_name, role, managed_by
            FROM user_accounts
            WHERE user_id = ?
            """
        )
        with self._connect() as connection:
            row = connection.execute(statement, (user_id,)).fetchone()
        if row:
            return dict(row)
        return {
            "user_id": user_id,
            "username": "",
            "first_name": "",
            "role": "user",
            "managed_by": None,
        }

    def set_role(
        self,
        user_id: int,
        role: str,
        *,
        managed_by: int | None = None,
    ) -> bool:
        if role not in {"admin", "beta_tester", "user"}:
            raise ValueError("Недопустимая роль")
        statement = self._sql(
            """
            UPDATE user_accounts
            SET role = ?, managed_by = ?, price_mode = 'live',
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND role <> 'owner'
            """
        )
        with self._connect() as connection:
            cursor = connection.execute(
                statement,
                (
                    role,
                    managed_by if role == "beta_tester" else None,
                    user_id,
                ),
            )
            changed = cursor.rowcount > 0
            connection.commit()
        return changed

    def get_price_mode(self, user_id: int) -> str:
        statement = self._sql(
            "SELECT price_mode FROM user_accounts WHERE user_id = ? AND role = 'owner'"
        )
        with self._connect() as connection:
            row = connection.execute(statement, (user_id,)).fetchone()
        return "test" if row and str(row["price_mode"]) == "test" else "live"

    def set_owner_price_mode(self, user_id: int, mode: str) -> bool:
        if mode not in {"live", "test"}:
            raise ValueError("Недопустимый режим цен")
        statement = self._sql(
            """
            UPDATE user_accounts
            SET price_mode = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND role = 'owner'
            """
        )
        with self._connect() as connection:
            cursor = connection.execute(statement, (mode, user_id))
            changed = cursor.rowcount > 0
            connection.commit()
        return changed

    def get_access_state(self, user_id: int) -> dict[str, Any]:
        now = datetime.now(UTC).replace(tzinfo=None)
        subscription_statement = self._sql(
            """
            SELECT plan_code, ends_at, source
            FROM subscriptions
            WHERE user_id = ? AND starts_at <= ? AND ends_at > ?
            ORDER BY ends_at DESC
            LIMIT 1
            """
        )
        account_statement = self._sql(
            """
            SELECT created_at
            FROM user_accounts
            WHERE user_id = ?
            """
        )
        with self._connect() as connection:
            subscription = connection.execute(
                subscription_statement,
                (user_id, self._db_datetime(now), self._db_datetime(now)),
            ).fetchone()
            if subscription:
                plan_code = str(subscription["plan_code"])
                return {
                    "active": True,
                    "plan_code": plan_code,
                    "plan_name": {
                        "standard": "Стандарт",
                        "pro": "Pro",
                    }.get(plan_code, plan_code),
                    "source": str(subscription["source"]),
                    "ends_at": self._as_datetime(subscription["ends_at"]),
                }
            account = connection.execute(account_statement, (user_id,)).fetchone()

        if not account:
            return {
                "active": False,
                "plan_code": "",
                "plan_name": "",
                "source": "",
                "ends_at": now,
            }
        trial_ends_at = self._as_datetime(account["created_at"]) + timedelta(days=7)
        return {
            "active": trial_ends_at > now,
            "plan_code": "trial",
            "plan_name": "Пробный",
            "source": "trial",
            "ends_at": trial_ends_at,
        }

    def record_star_payment(
        self,
        user_id: int,
        plan_code: str,
        duration_months: int,
        amount_stars: int,
        telegram_payment_charge_id: str,
        provider_payment_charge_id: str,
    ) -> datetime:
        now = datetime.now(UTC).replace(tzinfo=None)
        existing_statement = self._sql(
            """
            SELECT ends_at
            FROM subscriptions
            WHERE payment_charge_id = ?
            """
        )
        latest_statement = self._sql(
            """
            SELECT ends_at
            FROM subscriptions
            WHERE user_id = ?
            ORDER BY ends_at DESC
            LIMIT 1
            """
        )
        payment_statement = self._sql(
            """
            INSERT INTO star_payments (
                telegram_payment_charge_id,
                provider_payment_charge_id,
                user_id,
                plan_code,
                duration_months,
                amount_stars
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """
        )
        subscription_statement = self._sql(
            """
            INSERT INTO subscriptions (
                user_id,
                plan_code,
                starts_at,
                ends_at,
                source,
                payment_charge_id
            )
            VALUES (?, ?, ?, ?, 'stars', ?)
            """
        )
        with self._connect() as connection:
            existing = connection.execute(
                existing_statement, (telegram_payment_charge_id,)
            ).fetchone()
            if existing:
                return self._as_datetime(existing["ends_at"])

            latest = connection.execute(latest_statement, (user_id,)).fetchone()
            latest_end = self._as_datetime(latest["ends_at"]) if latest else now
            starts_at = max(now, latest_end)
            ends_at = starts_at + timedelta(days=30 * duration_months)
            connection.execute(
                payment_statement,
                (
                    telegram_payment_charge_id,
                    provider_payment_charge_id,
                    user_id,
                    plan_code,
                    duration_months,
                    amount_stars,
                ),
            )
            connection.execute(
                subscription_statement,
                (
                    user_id,
                    plan_code,
                    self._db_datetime(starts_at),
                    self._db_datetime(ends_at),
                    telegram_payment_charge_id,
                ),
            )
            connection.commit()
        return ends_at

    def save_leads(
        self,
        user_id: int,
        leads: list[Lead],
        project_id: int | None = None,
    ) -> list[int]:
        statement = self._sql(
            """
            INSERT INTO leads (
                user_id, name, source_url, website, phone, address,
                snippet, search_query, score, project_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, source_url) DO UPDATE SET
                name = excluded.name,
                website = excluded.website,
                phone = excluded.phone,
                address = excluded.address,
                snippet = excluded.snippet,
                search_query = excluded.search_query,
                score = excluded.score,
                project_id = COALESCE(excluded.project_id, leads.project_id)
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
                        project_id,
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

    def update_lead_status(self, user_id: int, lead_id: int, status: str) -> bool:
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
        self,
        user_id: int,
        name: str,
        niche: str,
        region: str,
        *,
        category_code: str = "custom",
        category_name: str = "Своя ниша",
        offer: str = "",
        target_audience: str = "",
        advantage: str = "",
    ) -> int:
        statement = self._sql(
            """
            INSERT INTO projects (
                user_id, name, category_code, category_name, niche, offer,
                target_audience, region, advantage, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
            RETURNING id
            """
        )
        with self._connect() as connection:
            row = connection.execute(
                statement,
                (
                    user_id,
                    name,
                    category_code,
                    category_name,
                    niche,
                    offer,
                    target_audience,
                    region,
                    advantage,
                ),
            ).fetchone()
            connection.commit()
        return int(row["id"])

    def list_projects(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        statement = self._sql(
            """
            SELECT id, name, category_code, category_name, niche, offer,
                   target_audience, region, advantage, status, created_at
            FROM projects
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """
        )
        with self._connect() as connection:
            rows = connection.execute(statement, (user_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def get_project(self, user_id: int, project_id: int) -> dict[str, Any] | None:
        statement = self._sql(
            """
            SELECT id, name, category_code, category_name, niche, offer,
                   target_audience, region, advantage, status, created_at
            FROM projects
            WHERE user_id = ? AND id = ?
            """
        )
        with self._connect() as connection:
            row = connection.execute(statement, (user_id, project_id)).fetchone()
        return dict(row) if row else None

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
    def _as_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        return datetime.fromisoformat(str(value))

    def _db_datetime(self, value: datetime) -> datetime | str:
        return value if self.is_postgres else value.isoformat(sep=" ")

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
