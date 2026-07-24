import tempfile
import unittest
import sqlite3
from pathlib import Path

from leadpilot.database import Database
from leadpilot.models import Lead


class DatabaseTests(unittest.TestCase):
    def test_saves_and_reads_lead(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(f"sqlite:///{Path(directory) / 'test.db'}")
            db.init_schema()
            ids = db.save_leads(
                42,
                [
                    Lead(
                        name="Компания",
                        source_url="https://example.com",
                        website="https://example.com",
                        query="тест",
                        score=70,
                    )
                ],
            )
            lead = db.get_lead(42, ids[0])
        self.assertIsNotNone(lead)
        self.assertEqual(lead.name, "Компания")
        self.assertEqual(lead.score, 70)

    def test_projects_radars_analytics_and_pipeline_status(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(f"sqlite:///{Path(directory) / 'features.db'}")
            db.init_schema()
            project_id = db.create_project(
                42, "Клиники Москвы", "стоматологии", "Москва"
            )
            radar_id = db.create_radar(
                42,
                ["стоматологии", "косметологии"],
                ["Москва", "Казань"],
                3,
            )
            lead_id = db.save_leads(
                42,
                [
                    Lead(
                        name="Компания",
                        source_url="https://example.com",
                        phone="+7 900 000-00-00",
                        score=90,
                    )
                ],
            )[0]
            changed = db.update_lead_status(42, lead_id, "contacted")
            stats = db.lead_statistics(42)
            projects = db.list_projects(42)
            radars = db.list_radars(42)
            radar = db.get_radar(42, radar_id)

        self.assertEqual(projects[0]["id"], project_id)
        self.assertEqual(radars[0]["id"], radar_id)
        self.assertEqual(radar["result_limit"], 3)
        self.assertTrue(changed)
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["high_score"], 1)
        self.assertEqual(stats["contacted_count"], 1)

    def test_adds_status_to_existing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            )
            connection.commit()
            connection.close()

            db = Database(f"sqlite:///{path}")
            db.init_schema()
            columns = {
                row[1]
                for row in sqlite3.connect(path).execute(
                    "PRAGMA table_info(leads)"
                )
            }

        self.assertIn("status", columns)


if __name__ == "__main__":
    unittest.main()
