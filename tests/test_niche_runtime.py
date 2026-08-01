import unittest
from pathlib import Path


class NicheRuntimeWiringTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.source = (root / "leadpilot" / "niche_profile.py").read_text(
            encoding="utf-8"
        )

    def test_feature_does_not_patch_menu_button_routing(self):
        self.assertNotIn("navigate_menu", self.source)
        self.assertNotIn("_button_pattern", self.source)
        self.assertNotIn("BUTTON_LEADS", self.source)
        self.assertNotIn("BUTTON_ANALYZE", self.source)
        self.assertNotIn("BUTTON_MESSAGE", self.source)

    def test_niche_input_uses_one_real_conversation(self):
        self.assertIn("ConversationHandler(", self.source)
        self.assertIn('pattern=rf"^{NICHE_CALLBACK}$"', self.source)
        self.assertIn("NICHE_INPUT_STATE", self.source)
        self.assertIn("bot_module.USER_INPUT_FILTER", self.source)
        self.assertEqual(self.source.count("group=-10"), 1)

    def test_niche_has_no_global_text_interceptor(self):
        self.assertNotIn("NICHE_PENDING_KEY", self.source)
        self.assertNotIn("ApplicationHandlerStop", self.source)
        self.assertNotIn("filters.TEXT & ~filters.COMMAND", self.source)

    def test_create_message_flow_keeps_existing_lead_state(self):
        self.assertIn(
            "self._lead_from_message(update, bot_module.MESSAGE_LEAD_ID)",
            self.source,
        )
        self.assertIn("return bot_module.MESSAGE_LEAD_ID", self.source)
        self.assertIn("return ConversationHandler.END", self.source)


if __name__ == "__main__":
    unittest.main()
