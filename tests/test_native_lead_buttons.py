import unittest
from pathlib import Path


class NativeLeadButtonRoutingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.bot_source = (self.root / "leadpilot" / "bot.py").read_text(
            encoding="utf-8"
        )
        self.main_source = (self.root / "leadpilot" / "__main__.py").read_text(
            encoding="utf-8"
        )
        self.niche_source = (self.root / "leadpilot" / "niche_profile.py").read_text(
            encoding="utf-8"
        )
        self.keyboard_source = (
            self.root / "leadpilot" / "hide_settings_button.py"
        ).read_text(encoding="utf-8")

    def test_native_conversation_handles_analysis_and_message_buttons(self):
        self.assertIn(
            "filters.Regex(_button_pattern(BUTTON_MESSAGE))",
            self.bot_source,
        )
        self.assertIn(
            "filters.Regex(_button_pattern(BUTTON_ANALYZE))",
            self.bot_source,
        )
        self.assertIn("self.message_start", self.bot_source)
        self.assertIn("self.analyze_start", self.bot_source)

    def test_native_standalone_handler_handles_leads_button(self):
        self.assertIn(
            "(BUTTON_LEADS, self.list_leads)",
            self.bot_source,
        )

    def test_reply_keyboard_keeps_original_three_labels(self):
        self.assertIn("bot_module.BUTTON_LEADS", self.keyboard_source)
        self.assertIn("bot_module.BUTTON_ANALYZE", self.keyboard_source)
        self.assertIn("bot_module.BUTTON_MESSAGE", self.keyboard_source)
        self.assertNotIn('"/leads"', self.keyboard_source)
        self.assertNotIn('"/analyze"', self.keyboard_source)
        self.assertNotIn('"/message"', self.keyboard_source)

    def test_no_competing_button_router_or_global_niche_interceptor(self):
        self.assertNotIn("lead_button_routing", self.main_source)
        self.assertNotIn("single_instance", self.main_source)
        self.assertNotIn("ApplicationHandlerStop", self.niche_source)
        self.assertNotIn("filters.TEXT & ~filters.COMMAND", self.niche_source)


if __name__ == "__main__":
    unittest.main()
