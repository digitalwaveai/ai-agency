import tempfile
import unittest
from pathlib import Path

from leadpilot.database import Database
from leadpilot.user_commands import (
    _load_users,
    _split_messages,
    _user_block,
    install_user_commands,
)


class UserCommandsTests(unittest.TestCase):
    def test_owner_user_list_contains_username_id_and_tariff(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(f"sqlite:///{Path(directory) / 'users.db'}")
            db.init_schema()
            db.ensure_account(1, username="owner", first_name="Владелец")
            db.ensure_owner(1)
            db.ensure_account(2, username="client", first_name="Клиент")
            db.record_star_payment(
                2,
                "standard",
                3,
                2500,
                "telegram-users-test",
                "provider-users-test",
            )

            users = _load_users(db, owner_user_id=1)

        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["user_id"], 2)
        text = _user_block(1, users[0])
        self.assertIn("@client", text)
        self.assertIn("ID: 2", text)
        self.assertIn("Тариф: Стандарт до", text)
        self.assertNotIn("@owner", text)

    def test_long_user_list_is_split_into_telegram_sized_messages(self):
        blocks = [f"{index}. Пользователь\n" + ("x" * 700) for index in range(12)]
        messages = _split_messages("👥 Пользователи бота: 12", blocks, limit=1000)

        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(message) <= 1000 for message in messages))

    def test_myid_and_users_handlers_are_registered(self):
        class FakeApplication:
            def __init__(self):
                self.handlers = []
                self.post_init = None

            def add_handler(self, handler, group=0):
                self.handlers.append((handler, group))

        class FakeBot:
            def build_application(self):
                return FakeApplication()

        install_user_commands(FakeBot)
        application = FakeBot().build_application()
        commands = {
            command
            for handler, _ in application.handlers
            for command in getattr(handler, "commands", ())
        }

        self.assertIn("myid", commands)
        self.assertIn("users", commands)
        self.assertTrue(all(group == -1 for _, group in application.handlers))
        self.assertIsNotNone(application.post_init)


if __name__ == "__main__":
    unittest.main()
