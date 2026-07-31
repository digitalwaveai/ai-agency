import tempfile
import unittest
from pathlib import Path

from leadpilot.database import Database
from leadpilot.models import Lead
from leadpilot.outreach import OutreachGenerator
from leadpilot.personal_profile import install_personal_profile


class DummyBot:
    def build_application(self):
        raise AssertionError("Application building is not needed in this test")


install_personal_profile(Database, DummyBot)


class PersonalProfileTests(unittest.TestCase):
    def test_profile_is_saved_per_user_and_can_be_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.sqlite"
            database = Database(f"sqlite:///{path}")
            database.init_schema()

            database.set_user_profile(
                101,
                "  Я создаю   AI-ботов и автоматизирую обработку заявок.  ",
            )
            database.set_user_profile(
                202,
                "Я занимаюсь дизайном карточек для маркетплейсов.",
            )

            self.assertEqual(
                database.get_user_profile(101),
                "Я создаю AI-ботов и автоматизирую обработку заявок.",
            )
            self.assertEqual(
                database.get_user_profile(202),
                "Я занимаюсь дизайном карточек для маркетплейсов.",
            )

            database.set_user_profile(
                101,
                "Я внедряю AI-ассистентов для малого бизнеса.",
            )
            self.assertEqual(
                database.get_user_profile(101),
                "Я внедряю AI-ассистентов для малого бизнеса.",
            )

    def test_fallback_has_requested_structure_and_sender_profile(self):
        lead = Lead(
            name="Салон Лаванда",
            source_url="https://www.google.com/maps/search/?api=1&query=lavanda",
            website="https://lavanda.example",
            phone="+7 000 000-00-00",
            address="Воронеж",
            snippet="Салон красоты, запись по телефону",
            query="салоны красоты Воронеж",
            score=90,
        )
        profile = (
            "Я создаю AI-ботов, которые помогают бизнесу быстрее отвечать "
            "на обращения и не терять заявки."
        )

        message = OutreachGenerator.fallback(lead, profile)
        paragraphs = [part.strip() for part in message.split("\n\n") if part.strip()]

        self.assertEqual(len(paragraphs), 5)
        self.assertIn("Google Картах", paragraphs[0])
        self.assertIn("часть обращений", paragraphs[1])
        self.assertIn("потенциальный клиент", paragraphs[2])
        self.assertIn("можно закрыть", paragraphs[3])
        self.assertIn(profile, paragraphs[4])

    def test_profile_sentence_does_not_duplicate_first_person(self):
        self.assertEqual(
            OutreachGenerator.profile_sentence(
                "Я помогаю салонам автоматизировать запись клиентов."
            ),
            "Я помогаю салонам автоматизировать запись клиентов.",
        )
        self.assertEqual(
            OutreachGenerator.profile_sentence("Разработка сайтов для бизнеса"),
            "Я занимаюсь следующим: Разработка сайтов для бизнеса",
        )


if __name__ == "__main__":
    unittest.main()
