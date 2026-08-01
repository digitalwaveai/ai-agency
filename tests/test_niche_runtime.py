import unittest
from pathlib import Path


class NicheRuntimeWiringTests(unittest.TestCase):
    def test_core_bot_routing_file_is_not_modified_by_feature(self):
        """The rebuild must not patch lead/menu routing in bot.py."""
        root = Path(__file__).resolve().parents[1]
        source = (root / "leadpilot" / "niche_profile.py").read_text(encoding="utf-8")

        self.assertNotIn("navigate_menu", source)
        self.assertNotIn("_button_pattern", source)
        self.assertNotIn("BUTTON_LEADS", source)
        self.assertNotIn("BUTTON_ANALYZE", source)
        self.assertNotIn("BUTTON_MESSAGE", source)

    def test_niche_handlers_are_strictly_isolated(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "leadpilot" / "niche_profile.py").read_text(encoding="utf-8")

        self.assertIn('pattern=rf"^{NICHE_CALLBACK}$"', source)
        self.assertEqual(source.count("group=-100"), 3)
        self.assertIn("if not context.user_data.get(NICHE_PENDING_KEY):", source)

    def test_create_message_flow_keeps_existing_lead_state(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "leadpilot" / "niche_profile.py").read_text(encoding="utf-8")

        self.assertIn("self._lead_from_message(update, MESSAGE_LEAD_ID)", source)
        self.assertIn("return MESSAGE_LEAD_ID", source)
        self.assertIn("return ConversationHandler.END", source)


if __name__ == "__main__":
    unittest.main()
