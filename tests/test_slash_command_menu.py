import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram import MenuButtonCommands

from leadpilot import bot as bot_module
from leadpilot.hide_settings_button import (
    TELEGRAM_COMMAND_MENU,
    register_telegram_command_menu,
    visible_menu_rows,
)


class TelegramCommandMenuTests(unittest.TestCase):
    def test_reply_keyboard_uses_original_visible_buttons(self):
        flattened = [button for row in visible_menu_rows() for button in row]
        self.assertIn(bot_module.BUTTON_LEADS, flattened)
        self.assertIn(bot_module.BUTTON_ANALYZE, flattened)
        self.assertIn(bot_module.BUTTON_MESSAGE, flattened)
        self.assertNotIn("/leads", flattened)
        self.assertNotIn("/analyze", flattened)
        self.assertNotIn("/message", flattened)

    def test_telegram_menu_contains_three_slash_commands(self):
        commands = [item.command for item in TELEGRAM_COMMAND_MENU]
        descriptions = [item.description for item in TELEGRAM_COMMAND_MENU]
        self.assertEqual(commands, ["leads", "analyze", "message"])
        self.assertEqual(
            descriptions,
            ["Мои лиды", "Анализ клиента", "Создать сообщение"],
        )


class TelegramCommandMenuRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_uses_telegram_command_menu_button(self):
        bot = SimpleNamespace(
            set_my_commands=AsyncMock(),
            set_chat_menu_button=AsyncMock(),
        )
        application = SimpleNamespace(bot=bot)

        await register_telegram_command_menu(application)

        bot.set_my_commands.assert_awaited_once_with(TELEGRAM_COMMAND_MENU)
        bot.set_chat_menu_button.assert_awaited_once()
        menu_button = bot.set_chat_menu_button.await_args.kwargs["menu_button"]
        self.assertIsInstance(menu_button, MenuButtonCommands)


if __name__ == "__main__":
    unittest.main()
