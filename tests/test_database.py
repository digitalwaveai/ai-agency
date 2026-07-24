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


if __name__ == "__main__":
    unittest.main()
