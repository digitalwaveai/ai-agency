import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram import MenuButtonCommands

from leadpilot.telegram_command_menu import (
    DEFAULT_COMMANDS,
    OWNER_COMMANDS,
    register_command_menu,
)


class TelegramCommandMenuTests(unittest.TestCase):
    def test_full_default_menu_contains_core_commands(self):
        commands = [item.command for item in DEFAULT_COMMANDS]
        for command in (
            "start",
            "menu",
            "find",
            "projects",
            "leads",
            "analyze",
            "message",
            "radars",
            "export",
            "analytics",
            "plans",
            "limits",
            "support",
            "help",
            "myid",
        ):
            with self.subTest(command=command):
                self.assertIn(command, commands)

    def test_owner_menu_keeps_default_and_owner_commands(self):
        default = {item.command for item in DEFAULT_COMMANDS}
        owner = {item.command for item in OWNER_COMMANDS}
        self.assertTrue(default.issubset(owner))
        self.assertIn("users", owner)
        self.assertIn("price_mode", owner)

    def test_reply_keyboard_contains_labels_not_slash_commands(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "leadpilot" / "hide_settings_button.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("bot_module.BUTTON_LEADS", source)
        self.assertIn("bot_module.BUTTON_ANALYZE", source)
        self.assertIn("bot_module.BUTTON_MESSAGE", source)
        self.assertNotIn('"/leads"', source)
        self.assertNotIn('"/analyze"', source)
        self.assertNotIn('"/message"', source)


class TelegramCommandMenuRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_restores_default_and_owner_scopes(self):
        bot = SimpleNamespace(
            set_my_commands=AsyncMock(),
            set_chat_menu_button=AsyncMock(),
        )
        application = SimpleNamespace(bot=bot)

        await register_command_menu(application, owner_user_id=123)

        self.assertEqual(bot.set_my_commands.await_count, 2)
        first_call = bot.set_my_commands.await_args_list[0]
        self.assertEqual(first_call.args[0], DEFAULT_COMMANDS)
        second_call = bot.set_my_commands.await_args_list[1]
        self.assertEqual(second_call.args[0], OWNER_COMMANDS)

        self.assertEqual(bot.set_chat_menu_button.await_count, 2)
        for call in bot.set_chat_menu_button.await_args_list:
            menu_button = call.kwargs["menu_button"]
            self.assertIsInstance(menu_button, MenuButtonCommands)


if __name__ == "__main__":
    unittest.main()
