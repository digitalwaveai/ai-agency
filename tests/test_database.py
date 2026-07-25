import sqlite3
import tempfile
import unittest
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
                for row in sqlite3.connect(path).execute("PRAGMA table_info(leads)")
            }

        self.assertIn("status", columns)

    def test_trial_and_star_payment_access_are_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(f"sqlite:///{Path(directory) / 'billing.db'}")
            db.init_schema()
            db.ensure_account(42, username="client", first_name="Клиент")

            trial = db.get_access_state(42)
            paid_until = db.record_star_payment(
                42,
                "standard",
                3,
                1400,
                "telegram-charge-1",
                "provider-charge-1",
            )
            repeated_until = db.record_star_payment(
                42,
                "standard",
                3,
                1400,
                "telegram-charge-1",
                "provider-charge-1",
            )
            paid = db.get_access_state(42)

        self.assertTrue(trial["active"])
        self.assertEqual(trial["plan_code"], "trial")
        self.assertTrue(paid["active"])
        self.assertEqual(paid["plan_code"], "standard")
        self.assertEqual(paid["source"], "stars")
        self.assertEqual(paid_until, repeated_until)

    def test_project_questionnaire_fields_and_project_leads_are_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "projects.db"
            db = Database(f"sqlite:///{path}")
            db.init_schema()
            project_id = db.create_project(
                42,
                "AI для бьюти",
                "косметологи",
                "Москва",
                category_code="ai_automation",
                category_name="AI и автоматизация",
                offer="AI-бот для заявок",
                target_audience="частные косметологи",
                advantage="не терять обращения",
            )
            lead_id = db.save_leads(
                42,
                [
                    Lead(
                        name="Клиника",
                        source_url="https://example.test/clinic",
                        query="косметологи Москва",
                    )
                ],
                project_id=project_id,
            )[0]
            project = db.get_project(42, project_id)
            row = (
                sqlite3.connect(path)
                .execute("SELECT project_id FROM leads WHERE id = ?", (lead_id,))
                .fetchone()
            )

        self.assertEqual(project["category_code"], "ai_automation")
        self.assertEqual(project["offer"], "AI-бот для заявок")
        self.assertEqual(project["target_audience"], "частные косметологи")
        self.assertEqual(project["advantage"], "не терять обращения")
        self.assertEqual(row[0], project_id)

    def test_owner_is_unique_and_price_mode_is_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(f"sqlite:///{Path(directory) / 'roles.db'}")
            db.init_schema()
            db.ensure_account(10)
            db.ensure_account(20)
            db.ensure_owner(10)
            self.assertEqual(db.get_role(10), "owner")
            self.assertTrue(db.set_owner_price_mode(10, "test"))
            self.assertEqual(db.get_price_mode(10), "test")
            self.assertFalse(db.set_owner_price_mode(20, "test"))

            db.ensure_owner(20)

            self.assertEqual(db.get_role(10), "user")
            self.assertEqual(db.get_price_mode(10), "live")
            self.assertEqual(db.get_role(20), "owner")

    def test_admin_can_own_beta_tester_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(f"sqlite:///{Path(directory) / 'team.db'}")
            db.init_schema()
            for user_id in (1, 2, 3):
                db.ensure_account(user_id)
            db.ensure_owner(1)
            db.set_role(2, "admin")
            db.set_role(3, "beta_tester", managed_by=2)
            beta = db.get_role_record(3)

            self.assertEqual(db.get_role(1), "owner")
            self.assertEqual(db.get_role(2), "admin")
            self.assertEqual(beta["role"], "beta_tester")
            self.assertEqual(beta["managed_by"], 2)


if __name__ == "__main__":
    unittest.main()
