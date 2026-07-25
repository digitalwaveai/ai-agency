import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from leadpilot.bot import LeadPilotBot, _star_tariffs
from leadpilot.config import Settings


class OwnerPricingTests(unittest.TestCase):
    def make_bot(self, database_url: str) -> LeadPilotBot:
        return LeadPilotBot(
            Settings(
                telegram_bot_token="123456:TEST_TOKEN",
                serpapi_key="",
                openai_api_key="",
                database_url=database_url,
                owner_telegram_id=42,
                demo_mode=True,
                search_provider="serpapi",
                openai_model="gpt-5.6-luna",
                support_username="@DigitalWave_vl",
            )
        )

    def test_test_prices_are_owner_only_and_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = self.make_bot(f"sqlite:///{Path(directory) / 'owner-pricing.db'}")
            bot.db.init_schema()
            bot.db.ensure_owner(42)
            bot.db.ensure_account(77)

            self.assertEqual(bot.price_mode_for(42), "live")
            self.assertEqual(bot.price_mode_for(77), "live")
            self.assertTrue(bot.db.set_owner_price_mode(42, "test"))
            self.assertEqual(bot.price_mode_for(42), "test")
            self.assertEqual(bot.price_mode_for(77), "live")
            self.assertFalse(bot.db.set_owner_price_mode(77, "test"))

    def test_switching_mode_invalidates_previous_owner_invoices(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = self.make_bot(f"sqlite:///{Path(directory) / 'mode-switch.db'}")
            bot.db.init_schema()
            bot.db.ensure_owner(42)
            bot.db.ensure_account(77)

            self.assertTrue(bot.payment_mode_allowed(42, "live"))
            self.assertFalse(bot.payment_mode_allowed(42, "test"))
            self.assertTrue(bot.payment_mode_allowed(77, "live"))
            self.assertFalse(bot.payment_mode_allowed(77, "test"))

            bot.db.set_owner_price_mode(42, "test")

            self.assertFalse(bot.payment_mode_allowed(42, "live"))
            self.assertTrue(bot.payment_mode_allowed(42, "test"))
            self.assertTrue(bot.payment_mode_allowed(77, "live"))
            self.assertFalse(bot.payment_mode_allowed(77, "test"))

    def test_test_and_live_invoice_payloads_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = self.make_bot(f"sqlite:///{Path(directory) / 'payload.db'}")
            bot.db.init_schema()
            self.assertEqual(
                bot._parse_payment_payload("leadpilot|standard|3|42|test"),
                ("standard", 3, 42, "test"),
            )
            self.assertEqual(
                bot._parse_payment_payload("leadpilot|pro|12|77|live"),
                ("pro", 12, 77, "live"),
            )
            expected_test_prices = {
                ("standard", 1): 1,
                ("standard", 3): 2,
                ("standard", 6): 3,
                ("standard", 12): 4,
                ("pro", 1): 2,
                ("pro", 3): 4,
                ("pro", 6): 6,
                ("pro", 12): 10,
            }
            self.assertEqual(
                {
                    option: stars
                    for option, (_, stars) in _star_tariffs("test").items()
                },
                expected_test_prices,
            )
            self.assertEqual(_star_tariffs("live")[("standard", 3)][1], 2500)
            self.assertEqual(_star_tariffs("live")[("pro", 12)][1], 22500)


class OwnerPricingCommandTests(unittest.IsolatedAsyncioTestCase):
    def make_bot(self, database_url: str) -> LeadPilotBot:
        return LeadPilotBot(
            Settings(
                telegram_bot_token="123456:TEST_TOKEN",
                serpapi_key="",
                openai_api_key="",
                database_url=database_url,
                owner_telegram_id=42,
                demo_mode=True,
                search_provider="serpapi",
                openai_model="gpt-5.6-luna",
                support_username="@DigitalWave_vl",
            )
        )

    @staticmethod
    def make_update(user_id: int) -> SimpleNamespace:
        return SimpleNamespace(
            effective_user=SimpleNamespace(
                id=user_id,
                username="tester",
                first_name="Tester",
            ),
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
        )

    async def test_owner_can_switch_test_and_live_prices_with_slash_command(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = self.make_bot(f"sqlite:///{Path(directory) / 'command.db'}")
            bot.db.init_schema()
            update = self.make_update(42)

            await bot.price_mode(update, SimpleNamespace(args=["test"]))
            self.assertEqual(bot.price_mode_for(42), "test")

            await bot.price_mode(update, SimpleNamespace(args=["live"]))
            self.assertEqual(bot.price_mode_for(42), "live")

    async def test_non_owner_cannot_enable_test_prices_with_slash_command(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = self.make_bot(f"sqlite:///{Path(directory) / 'blocked.db'}")
            bot.db.init_schema()
            update = self.make_update(77)

            await bot.price_mode(update, SimpleNamespace(args=["test"]))

            self.assertEqual(bot.price_mode_for(77), "live")
            self.assertIn(
                "Команда недоступна.",
                update.effective_message.reply_text.await_args.args[0],
            )


if __name__ == "__main__":
    unittest.main()
