import unittest

from leadpilot.hide_settings_button import (
    ANALYZE_COMMAND_BUTTON,
    LEADS_COMMAND_BUTTON,
    MESSAGE_COMMAND_BUTTON,
    visible_menu_rows,
)


class SlashCommandMenuTests(unittest.TestCase):
    def test_lead_actions_are_exact_working_commands(self):
        self.assertEqual(LEADS_COMMAND_BUTTON, "/leads")
        self.assertEqual(ANALYZE_COMMAND_BUTTON, "/analyze")
        self.assertEqual(MESSAGE_COMMAND_BUTTON, "/message")

    def test_menu_contains_each_command_once(self):
        flattened = [button for row in visible_menu_rows() for button in row]
        for command in (
            LEADS_COMMAND_BUTTON,
            ANALYZE_COMMAND_BUTTON,
            MESSAGE_COMMAND_BUTTON,
        ):
            with self.subTest(command=command):
                self.assertEqual(flattened.count(command), 1)

    def test_old_text_buttons_are_not_shown_for_these_actions(self):
        flattened = [button for row in visible_menu_rows() for button in row]
        self.assertNotIn("📋 Мои лиды", flattened)
        self.assertNotIn("💎 Анализ клиента", flattened)
        self.assertNotIn("✉️ Создать сообщение", flattened)


if __name__ == "__main__":
    unittest.main()
