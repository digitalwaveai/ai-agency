import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from leadpilot.usage_integrity import install_usage_integrity


class FakeDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.start = datetime.now().replace(microsecond=0) - timedelta(hours=1)
        self.end = self.start + timedelta(days=7)

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _sql(statement: str) -> str:
        return statement

    @staticmethod
    def _db_datetime(value: datetime) -> str:
        return value.isoformat(sep=" ")

    @staticmethod
    def _as_datetime(value):
        return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))

    def init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE user_accounts (
                    user_id INTEGER PRIMARY KEY,
                    created_at TIMESTAMP
                );
                CREATE TABLE subscriptions (
                    user_id INTEGER,
                    starts_at TIMESTAMP,
                    ends_at TIMESTAMP
                );
                CREATE TABLE leads (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER,
                    created_at TIMESTAMP
                );
                CREATE TABLE radars (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER,
                    created_at TIMESTAMP
                );
                CREATE TABLE usage_counters (
                    user_id INTEGER,
                    period_key TEXT,
                    searches INTEGER,
                    leads INTEGER,
                    analyses INTEGER,
                    messages INTEGER,
                    radars INTEGER,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, period_key)
                );
                """
            )
            connection.execute(
                "INSERT INTO user_accounts VALUES (?, ?)",
                (1, self._db_datetime(self.start)),
            )
            connection.execute(
                """
                INSERT INTO usage_counters
                    (user_id, period_key, searches, leads, analyses, messages, radars)
                VALUES (1, 'trial', 0, 0, 0, 0, 0)
                """
            )
            connection.commit()

    def get_usage_snapshot(self, user_id: int):
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT searches, leads, analyses, messages, radars
                FROM usage_counters
                WHERE user_id = ? AND period_key = 'trial'
                """,
                (user_id,),
            ).fetchone()
        fields = ("searches", "leads", "analyses", "messages", "radars")
        used = {field: int(row[field]) for field in fields}
        totals = {
            "searches": 10,
            "leads": 10,
            "analyses": 10,
            "messages": 10,
            "radars": 3,
        }
        return {
            "active": True,
            "unlimited": False,
            "access": {"source": "trial", "ends_at": self.end},
            "period_key": "trial",
            "used": used,
            "totals": totals,
            "remaining": {
                field: totals[field] - used[field]
                for field in totals
            },
        }

    def consume_usage(self, user_id: int, field: str, amount: int = 1):
        snapshot = self.get_usage_snapshot(user_id)
        if snapshot["remaining"][field] < amount:
            return False, snapshot
        with self._connect() as connection:
            connection.execute(
                f"UPDATE usage_counters SET {field} = {field} + ? WHERE user_id = ?",
                (amount, user_id),
            )
            connection.commit()
        return True, self.get_usage_snapshot(user_id)

    def save_leads(self, user_id: int, leads: list, project_id=None):
        return []


install_usage_integrity(FakeDatabase)


class UsageIntegrityTests(unittest.TestCase):
    def test_snapshot_repairs_persisted_lead_and_radar_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            database = FakeDatabase(Path(directory) / "usage.sqlite")
            database.init_schema()
            with database._connect() as connection:
                for lead_id in range(1, 4):
                    connection.execute(
                        "INSERT INTO leads VALUES (?, 1, ?)",
                        (
                            lead_id,
                            database._db_datetime(
                                database.start + timedelta(minutes=lead_id)
                            ),
                        ),
                    )
                connection.execute(
                    "INSERT INTO radars VALUES (1, 1, ?)",
                    (
                        database._db_datetime(
                            database.start + timedelta(minutes=10)
                        ),
                    ),
                )
                connection.commit()

            snapshot = database.get_usage_snapshot(1)

            self.assertEqual(snapshot["used"]["leads"], 3)
            self.assertEqual(snapshot["used"]["radars"], 1)

    def test_reconciliation_runs_before_new_quota_consumption(self):
        with tempfile.TemporaryDirectory() as directory:
            database = FakeDatabase(Path(directory) / "quota.sqlite")
            database.init_schema()
            with database._connect() as connection:
                for lead_id in range(1, 10):
                    connection.execute(
                        "INSERT INTO leads VALUES (?, 1, ?)",
                        (
                            lead_id,
                            database._db_datetime(
                                database.start + timedelta(minutes=lead_id)
                            ),
                        ),
                    )
                connection.commit()

            allowed, snapshot = database.consume_usage(1, "leads", 2)

            self.assertFalse(allowed)
            self.assertEqual(snapshot["used"]["leads"], 9)
            self.assertEqual(snapshot["remaining"]["leads"], 1)

    def test_reconciliation_never_reduces_recorded_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            database = FakeDatabase(Path(directory) / "monotonic.sqlite")
            database.init_schema()
            with database._connect() as connection:
                connection.execute(
                    "UPDATE usage_counters SET leads = 7 WHERE user_id = 1"
                )
                connection.commit()

            snapshot = database.get_usage_snapshot(1)

            self.assertEqual(snapshot["used"]["leads"], 7)


if __name__ == "__main__":
    unittest.main()
