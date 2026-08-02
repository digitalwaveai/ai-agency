import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from leadpilot.database import Database
from leadpilot.one_time_service_notice import (
    BROADCAST_ID,
    BROADCAST_TEXT,
    _ensure_schema,
    _is_completed,
    _recipient_ids,
    send_one_time_notice,
)


class OneTimeServiceNoticeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        path = Path(self.temporary_directory.name) / "notice.sqlite"
        self.database = Database(f"sqlite:///{path}")
        self.database.init_schema()
        self.database.ensure_account(101, username="first")
        self.database.ensure_account(202, username="second")
        self.bot = SimpleNamespace(send_message=AsyncMock())
        self.application = SimpleNamespace(bot=self.bot)

    def tearDown(self):
        self.temporary_directory.cleanup()

    async def test_notice_is_sent_to_each_existing_account_only_once(self):
        with patch("leadpilot.one_time_service_notice.asyncio.sleep", AsyncMock()):
            await send_one_time_notice(self.application, self.database)
            await send_one_time_notice(self.application, self.database)

        self.assertEqual(self.bot.send_message.await_count, 2)
        calls = self.bot.send_message.await_args_list
        self.assertEqual({call.kwargs["chat_id"] for call in calls}, {101, 202})
        self.assertTrue(all(call.kwargs["text"] == BROADCAST_TEXT for call in calls))
        self.assertTrue(_is_completed(self.database, BROADCAST_ID))

    async def test_users_added_after_completion_do_not_receive_old_notice(self):
        with patch("leadpilot.one_time_service_notice.asyncio.sleep", AsyncMock()):
            await send_one_time_notice(self.application, self.database)
            self.database.ensure_account(303, username="later")
            await send_one_time_notice(self.application, self.database)

        self.assertEqual(self.bot.send_message.await_count, 2)
        delivered = {
            call.kwargs["chat_id"] for call in self.bot.send_message.await_args_list
        }
        self.assertNotIn(303, delivered)
        self.assertEqual(_recipient_ids(self.database), [101, 202, 303])

    async def test_failed_delivery_is_recorded_and_not_retried(self):
        self.bot.send_message.side_effect = [RuntimeError("blocked"), None]
        with patch("leadpilot.one_time_service_notice.asyncio.sleep", AsyncMock()):
            await send_one_time_notice(self.application, self.database)
            await send_one_time_notice(self.application, self.database)

        self.assertEqual(self.bot.send_message.await_count, 2)
        with self.database._connect() as connection:
            rows = connection.execute(
                """
                SELECT user_id, status FROM system_broadcast_deliveries
                WHERE broadcast_id = ? ORDER BY user_id
                """,
                (BROADCAST_ID,),
            ).fetchall()
        statuses = {int(row["user_id"]): str(row["status"]) for row in rows}
        self.assertEqual(statuses, {101: "failed", 202: "sent"})

    def test_notice_text_names_exactly_three_unavailable_functions(self):
        self.assertIn("📋 Мои лиды", BROADCAST_TEXT)
        self.assertIn("💎 Анализ клиента", BROADCAST_TEXT)
        self.assertIn("✉️ Создать сообщение", BROADCAST_TEXT)
        self.assertIn("Остальные функции бота продолжают работать", BROADCAST_TEXT)

    def test_schema_can_be_created_repeatedly(self):
        _ensure_schema(self.database)
        _ensure_schema(self.database)
        self.assertFalse(_is_completed(self.database, BROADCAST_ID))


if __name__ == "__main__":
    unittest.main()
