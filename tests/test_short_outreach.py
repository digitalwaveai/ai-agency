import unittest

from leadpilot.models import Lead
from leadpilot.outreach import OutreachGenerator


class OutreachLengthTests(unittest.TestCase):
    def test_fallback_keeps_original_five_parts_at_medium_length(self):
        lead = Lead(
            id=12,
            name="Салон Лаванда",
            source_url="https://www.google.com/maps/place/example",
            website="https://lavanda.example",
            address="Воронеж",
            snippet="Салон красоты с онлайн-записью и несколькими мастерами",
            query="салоны красоты Воронеж",
            score=90,
        )
        niche = "Я создаю AI-ботов для быстрой обработки заявок."

        message = OutreachGenerator.fallback(lead, niche)
        paragraphs = [part.strip() for part in message.split("\n\n") if part.strip()]

        self.assertEqual(len(paragraphs), 5)
        self.assertIn("Google Картах", paragraphs[0])
        self.assertIn("Обратил внимание", paragraphs[0])
        self.assertIn("часть обращений", paragraphs[1])
        self.assertIn("потенциальный клиент", paragraphs[2])
        self.assertIn("можно закрыть", paragraphs[3])
        self.assertIn(niche, paragraphs[4])
        self.assertIn("решил написать именно вам", paragraphs[4])
        self.assertGreaterEqual(len(message), 600)
        self.assertLessEqual(len(message), 850)

    def test_prompt_restores_five_part_structure_and_600_800_length(self):
        source = OutreachGenerator.generate.__code__.co_consts
        combined = " ".join(value for value in source if isinstance(value, str))

        self.assertIn("600–800 знаков", combined)
        self.assertIn("пяти коротких абзацев", combined)
        self.assertIn("Зацепка: где увидели бизнес и почему он привлёк внимание", combined)
        self.assertIn("Раскрой проблему: объясни, к чему она может приводить", combined)
        self.assertIn("Простыми словами расскажи, как эту проблему можно закрыть", combined)
        self.assertNotIn("350–550 знаков", combined)


if __name__ == "__main__":
    unittest.main()
