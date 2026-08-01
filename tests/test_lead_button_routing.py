import re
import unittest

from leadpilot import bot as bot_module
from leadpilot.lead_button_routing import (
    button_key,
    canonical_button,
    semantic_body,
)


class LeadButtonRoutingTests(unittest.TestCase):
    def test_visible_words_are_normalized(self):
        self.assertEqual(button_key("📋  Мои\u200b лиды"), "мои лиды")
        self.assertEqual(button_key("💎️ Анализ клиента"), "анализ клиента")
        self.assertEqual(button_key("✉ Создать\u2060 сообщение"), "создать сообщение")

    def test_all_three_buttons_resolve_to_canonical_labels(self):
        variants = {
            "📋 Мои лиды": bot_module.BUTTON_LEADS,
            "📋️  МОИ   ЛИДЫ": bot_module.BUTTON_LEADS,
            "💎 Анализ клиента": bot_module.BUTTON_ANALYZE,
            "💎️ Анализ\u200b клиента": bot_module.BUTTON_ANALYZE,
            "✉️ Создать сообщение": bot_module.BUTTON_MESSAGE,
            "✉ Создать\u2060 сообщение": bot_module.BUTTON_MESSAGE,
        }
        for raw, expected in variants.items():
            with self.subTest(raw=raw):
                self.assertEqual(canonical_button(raw), expected)

    def test_semantic_patterns_accept_telegram_unicode_variants(self):
        variants = (
            (bot_module.BUTTON_LEADS, "📋️ Мои\u200b лиды"),
            (bot_module.BUTTON_ANALYZE, "💎 Анализ   клиента"),
            (bot_module.BUTTON_MESSAGE, "✉ Создать\u2060 сообщение"),
        )
        for canonical, raw in variants:
            with self.subTest(canonical=canonical, raw=raw):
                body = semantic_body(canonical)
                self.assertIsNotNone(body)
                self.assertIsNotNone(re.fullmatch(rf"(?iu:{body})", raw))

    def test_unrelated_menu_text_is_not_remapped(self):
        self.assertIsNone(canonical_button(bot_module.BUTTON_SEARCH))
        self.assertIsNone(semantic_body(bot_module.BUTTON_SEARCH))


if __name__ == "__main__":
    unittest.main()
