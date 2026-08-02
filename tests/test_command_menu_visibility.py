import unittest

from leadpilot.telegram_command_menu import DEFAULT_COMMANDS, OWNER_COMMANDS


class CommandMenuVisibilityTests(unittest.TestCase):
    def test_regular_users_do_not_see_hidden_commands(self):
        commands = {item.command for item in DEFAULT_COMMANDS}

        self.assertNotIn("radar_run", commands)
        self.assertNotIn("role", commands)
        self.assertNotIn("status", commands)
        self.assertNotIn("cancel", commands)
        self.assertNotIn("users", commands)

    def test_owner_sees_only_status_and_users_from_hidden_set(self):
        commands = {item.command for item in OWNER_COMMANDS}

        self.assertIn("status", commands)
        self.assertIn("users", commands)
        self.assertNotIn("radar_run", commands)
        self.assertNotIn("role", commands)
        self.assertNotIn("cancel", commands)

    def test_other_main_commands_remain_visible(self):
        commands = {item.command for item in DEFAULT_COMMANDS}

        for expected in {
            "start",
            "menu",
            "new_project",
            "projects",
            "find",
            "leads",
            "analyze",
            "message",
            "radars",
            "export",
            "analytics",
            "plans",
            "limits",
            "support",
            "myid",
            "help",
        }:
            self.assertIn(expected, commands)


if __name__ == "__main__":
    unittest.main()
