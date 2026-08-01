import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram.ext import ApplicationHandlerStop, ConversationHandler, MessageHandler

from leadpilot import bot as bot_module
from leadpilot.lead_action_buttons import (
    ACTION_ANALYZE,
    ACTION_MESSAGE,
    PENDING_KEY,
    ROUTING_GROUP,
    _visible_words,
    install_lead_action_buttons,
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


install_lead_action_buttons(FakeBot)


def make_update(text):
    return SimpleNamespace(
        effective_message=SimpleNamespace(text=text),
    )


class LeadActionButtonRegistrationTests(unittest.TestCase):
    def test_router_is_registered_before_niche_and_main_handlers(self):
        bot = FakeBot()
        application = bot.build_application()
        self.assertEqual(len(application.added), 1)
        group, handler = application.added[0]
        self.assertEqual(group, ROUTING_GROUP)
        self.assertLess(group, -10)
        self.assertIsInstance(handler, MessageHandler)

    def test_visible_text_normalization_ignores_emoji_variants(self):
        self.assertEqual(_visible_words("📋️  Мои\u200b лиды"), "мои лиды")
        self.assertEqual(_visible_words("💎 Анализ   клиента"), "анализ клиента")
        self.assertEqual(
            _visible_words("✉ Создать\u2060 сообщение"),
            "создать сообщение",
        )


class LeadActionButtonFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = FakeBot()
        self.context = SimpleNamespace(user_data={})

    async def test_leads_button_calls_existing_list_method(self):
        update = make_update("📋 Мои лиды")
        with self.assertRaises(ApplicationHandlerStop):
            await self.bot.route_lead_action_buttons(update, self.context)
        self.bot.list_leads.assert_awaited_once_with(update, self.context)

    async def test_analysis_button_and_id_use_existing_methods(self):
        start_update = make_update("💎 Анализ клиента")
        with self.assertRaises(ApplicationHandlerStop):
            await self.bot.route_lead_action_buttons(start_update, self.context)
        self.assertEqual(self.context.user_data[PENDING_KEY], ACTION_ANALYZE)

        id_update = make_update("17")
        with self.assertRaises(ApplicationHandlerStop):
            await self.bot.route_lead_action_buttons(id_update, self.context)
        self.bot.receive_analyze_lead_id.assert_awaited_once_with(
            id_update,
            self.context,
        )
        self.assertNotIn(PENDING_KEY, self.context.user_data)

    async def test_message_button_keeps_waiting_after_invalid_id(self):
        start_update = make_update("✉️ Создать сообщение")
        with self.assertRaises(ApplicationHandlerStop):
            await self.bot.route_lead_action_buttons(start_update, self.context)
        self.assertEqual(self.context.user_data[PENDING_KEY], ACTION_MESSAGE)

        self.bot.receive_lead_id.return_value = bot_module.MESSAGE_LEAD_ID
        invalid_update = make_update("не ID")
        with self.assertRaises(ApplicationHandlerStop):
            await self.bot.route_lead_action_buttons(invalid_update, self.context)
        self.assertEqual(self.context.user_data[PENDING_KEY], ACTION_MESSAGE)

    async def test_unrelated_button_is_passed_to_normal_handlers(self):
        update = make_update(bot_module.BUTTON_SEARCH)
        result = await self.bot.route_lead_action_buttons(update, self.context)
        self.assertIsNone(result)
        self.bot.list_leads.assert_not_awaited()
        self.bot.analyze_start.assert_not_awaited()
        self.bot.message_start.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
