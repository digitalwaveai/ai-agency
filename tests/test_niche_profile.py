import tempfile
import unittest
from pathlib import Path

from leadpilot.database import Database
from leadpilot.models import Lead
from leadpilot.niche_profile import clean_niche, install_niche_profile, niche_keyboard
from leadpilot.outreach import OutreachGenerator


class DummyBot:
    def build_application(self):
        raise AssertionError("Application building is not needed in this test")


install_niche_profile(Database, DummyBot)


class NicheProfileTests(unittest.TestCase):
    def test_niche_is_saved_separately_for_each_user(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "niches.sqlite"
            database = Database(f"sqlite:///{path}")
            database.init_schema()

            database.set_user_niche(
                101,
                "  Я создаю   AI-ботов для малого бизнеса.  ",
            )
            database.set_user_niche(
                202,
                "Я занимаюсь дизайном карточек для маркетплейсов.",
            )

            self.assertEqual(
                database.get_user_niche(101),
                "Я создаю AI-ботов для малого бизнеса.",
            )
            self.assertEqual(
                database.get_user_niche(202),
                "Я занимаюсь дизайном карточек для маркетплейсов.",
            )

            database.set_user_niche(
                101,
                "Я внедряю AI-ассистентов для обработки заявок.",
            )
            self.assertEqual(
                database.get_user_niche(101),
                "Я внедряю AI-ассистентов для обработки заявок.",
            )

    def test_niche_text_is_normalized(self):
        self.assertEqual(
            clean_niche("  Разработка   сайтов\nи ботов  "),
            "Разработка сайтов и ботов",
        )

    def test_start_keyboard_has_expected_labels(self):
        empty_button = niche_keyboard(False).inline_keyboard[0][0]
        filled_button = niche_keyboard(True).inline_keyboard[0][0]
        self.assertEqual(empty_button.text, "➕ Добавить нишу")
        self.assertEqual(filled_button.text, "✏️ Изменить нишу")
        self.assertEqual(empty_button.callback_data, "niche_profile:add")

    def test_fallback_has_five_requested_parts_and_sender_niche(self):
        lead = Lead(
            name="Салон Лаванда",
            source_url="https://www.google.com/maps/search/?api=1&query=lavanda",
            website="https://lavanda.example",
            phone="+7 000 000-00-00",
            address="Воронеж",
            snippet="Салон красоты с онлайн-записью",
            query="салоны красоты Воронеж",
            score=90,
        )
        sender_niche = (
            "Я создаю AI-ботов, которые помогают бизнесу быстрее отвечать "
            "на обращения и не терять заявки."
        )

        message = OutreachGenerator.fallback(lead, sender_niche)
        paragraphs = [part.strip() for part in message.split("\n\n") if part.strip()]

        self.assertEqual(len(paragraphs), 5)
        self.assertIn("Google Картах", paragraphs[0])
        self.assertIn("часть обращений", paragraphs[1])
        self.assertIn("потенциальный клиент", paragraphs[2])
        self.assertIn("можно закрыть", paragraphs[3])
        self.assertIn(sender_niche, paragraphs[4])

    def test_clean_install_order_does_not_restore_old_button_hotfixes(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "leadpilot" / "__main__.py").read_text(encoding="utf-8")

        self.assertNotIn("message_button_hotfix", source)
        self.assertNotIn("personal_profile", source)
        self.assertIn("install_niche_profile", source)
        self.assertLess(
            source.index("install_niche_profile(Database, LeadPilotBot)"),
            source.index("install_usage_limits(LeadPilotBot, Database)"),
        )


if __name__ == "__main__":
    unittest.main()
