import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram.ext import ApplicationHandlerStop, ConversationHandler

from leadpilot import bot as bot_module
from leadpilot.lead_button_routing import (
    ROUTING_GROUP,
    button_key,
    canonical_button,
    install_lead_button_routing,
    semantic_body,
)


class FakeApplication:
    def __init__(self):
        self.added = []

    def add_handler(self, handler, group=0):
        self.added.append((group, handler))


class FakeBot:
    def __init__(self):
        self.application = FakeApplication()
        self.list_leads = AsyncMock()
        self.analyze_start = AsyncMock(return_value=bot_module.ANALYZE_LEAD_ID)
        self.message_start = AsyncMock(return_value=bot_module.MESSAGE_LEAD_ID)
        self.receive_analyze_lead_id = AsyncMock(
            return_value=ConversationHandler.END
        )
        self.receive_lead_id = AsyncMock(return_value=ConversationHandler.END)

    def build_application(self):
        return self.application


install_lead_button_routing(FakeBot)


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

    def test_dedicated_conversation_has_highest_priority(self):
        bot = FakeBot()
        application = bot.build_application()
        self.assertEqual(len(application.added), 1)
        group, handler = application.added[0]
        self.assertEqual(group, ROUTING_GROUP)
        self.assertLess(group, -100)
        self.assertIsInstance(handler, ConversationHandler)
        self.assertEqual(len(handler.entry_points), 3)
        self.assertIn(bot_module.ANALYZE_LEAD_ID, handler.states)
        self.assertIn(bot_module.MESSAGE_LEAD_ID, handler.states)
        self.assertTrue(handler.allow_reentry)


class LeadButtonCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = FakeBot()
        self.context = SimpleNamespace(user_data={"old_state": True})
        self.update = SimpleNamespace()

    async def test_leads_button_calls_existing_function_and_stops_lower_groups(self):
        with self.assertRaises(ApplicationHandlerStop) as caught:
            await self.bot.open_leads_button(
                self.update,
                self.context,
            )
        self.bot.list_leads.assert_awaited_once_with(self.update, self.context)
        self.assertEqual(caught.exception.state, ConversationHandler.END)
        self.assertEqual(self.context.user_data, {})

    async def test_analysis_button_enters_existing_analysis_id_state(self):
        with self.assertRaises(ApplicationHandlerStop) as caught:
            await self.bot.open_analysis_button(
                self.update,
                self.context,
            )
        self.bot.analyze_start.assert_awaited_once_with(self.update, self.context)
        self.assertEqual(caught.exception.state, bot_module.ANALYZE_LEAD_ID)

    async def test_message_button_enters_existing_message_id_state(self):
        with self.assertRaises(ApplicationHandlerStop) as caught:
            await self.bot.open_message_button(
                self.update,
                self.context,
            )
        self.bot.message_start.assert_awaited_once_with(self.update, self.context)
        self.assertEqual(caught.exception.state, bot_module.MESSAGE_LEAD_ID)

    async def test_id_handlers_finish_through_existing_functions(self):
        with self.assertRaises(ApplicationHandlerStop) as analysis_stop:
            await self.bot.receive_analysis_button_id(
                self.update,
                self.context,
            )
        self.assertEqual(analysis_stop.exception.state, ConversationHandler.END)

        with self.assertRaises(ApplicationHandlerStop) as message_stop:
            await self.bot.receive_message_button_id(
                self.update,
                self.context,
            )
        self.assertEqual(message_stop.exception.state, ConversationHandler.END)


if __name__ == "__main__":
    unittest.main()
