import unittest

from leadpilot.telegram_command_menu import DEFAULT_COMMANDS, OWNER_COMMANDS


class CommandMenuVisibilityTests(unittest.TestCase):
    def test_regular_user_menu_matches_compact_legacy_list_plus_help(self):
        commands = [item.command for item in DEFAULT_COMMANDS]

        self.assertEqual(
            commands,
            [
                "start",
                "menu",
                "plans",
                "limits",
                "support",
                "myid",
                "help",
            ],
        )

    def test_owner_has_only_users_and_status_in_addition(self):
        regular = [item.command for item in DEFAULT_COMMANDS]
        owner = [item.command for item in OWNER_COMMANDS]

        self.assertEqual(owner, regular + ["users", "status"])

    def test_functional_commands_are_hidden_not_removed_from_bot(self):
        visible = {item.command for item in DEFAULT_COMMANDS}

        for hidden in {
            "new_project",
            "projects",
            "find",
            "leads",
            "analyze",
            "message",
            "radars",
            "radar_run",
            "export",
            "analytics",
            "role",
            "cancel",
        }:
            self.assertNotIn(hidden, visible)


if __name__ == "__main__":
    unittest.main()
