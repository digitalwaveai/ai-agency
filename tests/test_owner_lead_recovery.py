import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from leadpilot.database import Database
from leadpilot.lead_action_buttons import ROUTING_GROUP
from leadpilot.owner_lead_recovery import (
    _repair_user_leads,
    install_owner_lead_recovery,
)


class OwnerLeadDataRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / "owner-leads.sqlite"
        self.database = Database(f"sqlite:///{path}")
        self.database.init_schema()
        with self.database._connect() as connection:
            connection.execute("ALTER TABLE leads ADD COLUMN user_lead_id BIGINT")
            for source, personal_id in (
                ("https://one.test", 7),
                ("https://two.test", None),
                ("https://three.test", -2),
            ):
                connection.execute(
                    """
                    INSERT INTO leads (
                        user_id, user_lead_id, name, source_url, website,
                        phone, address, snippet, search_query, score, status
                    )
                    VALUES (?, ?, ?, ?, '', '', '', '', '', 0, 'new')
                    """,
                    (100, personal_id, source, source),
                )
            connection.commit()

    def tearDown(self):
        self.temp.cleanup()

    def test_repair_preserves_valid_id_and_fixes_only_invalid_ids(self):
        _repair_user_leads(self.database, 100)
        with self.database._connect() as connection:
            rows = connection.execute(
                """
                SELECT source_url, user_lead_id
                FROM leads WHERE user_id = 100 ORDER BY id
                """
            ).fetchall()

        values = {str(row["source_url"]): int(row["user_lead_id"]) for row in rows}
        self.assertEqual(values["https://one.test"], 7)
        self.assertEqual(
            {values["https://two.test"], values["https://three.test"]},
            {1, 2},
        )

    def test_repair_does_not_touch_another_user(self):
        with self.database._connect() as connection:
            connection.execute(
                """
                INSERT INTO leads (
                    user_id, user_lead_id, name, source_url, website,
                    phone, address, snippet, search_query, score, status
                )
                VALUES (200, NULL, 'Other', 'https://other.test', '', '', '', '', '', 0, 'new')
                """
            )
            connection.commit()

        _repair_user_leads(self.database, 100)
        with self.database._connect() as connection:
            row = connection.execute(
                "SELECT user_lead_id FROM leads WHERE user_id = 200"
            ).fetchone()
        self.assertIsNone(row["user_lead_id"])


class FakeBot:
    def __init__(self, owner: bool):
        self.owner = owner
        self.db = SimpleNamespace(repair_user_leads=AsyncMock())

    def is_owner(self, update):
        return self.owner

    async def list_leads(self, update, context):
        return "listed"

    async def message_start(self, update, context):
        return 4

    async def analyze_start(self, update, context):
        return 12

    async def receive_lead_id(self, update, context):
        return -1

    async def receive_analyze_lead_id(self, update, context):
        return -1


install_owner_lead_recovery(FakeBot, type("FakeDatabase", (), {}))


class OwnerLeadWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_is_owner_only(self):
        update = SimpleNamespace(effective_user=SimpleNamespace(id=123))
        context = SimpleNamespace()

        owner = FakeBot(True)
        regular = FakeBot(False)
        await owner.list_leads(update, context)
        await regular.list_leads(update, context)

        owner.db.repair_user_leads.assert_awaited_once_with(123)
        regular.db.repair_user_leads.assert_not_awaited()

    def test_button_router_has_absolute_priority(self):
        self.assertLessEqual(ROUTING_GROUP, -10000)


if __name__ == "__main__":
    unittest.main()
