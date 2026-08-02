import unittest

from leadpilot.models import Lead
from leadpilot.outreach import OutreachGenerator


class ShortOutreachTests(unittest.TestCase):
    def test_fallback_keeps_five_parts_but_stays_compact(self):
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
        self.assertIn("часть обращений", paragraphs[1])
        self.assertIn("потенциальный клиент", paragraphs[2])
        self.assertIn("можно закрыть", paragraphs[3])
        self.assertIn(niche, paragraphs[4])
        self.assertLessEqual(len(message), 650)

    def test_prompt_requests_short_total_length(self):
        source = OutreachGenerator.generate.__code__.co_consts
        combined = " ".join(value for value in source if isinstance(value, str))

        self.assertIn("350–550 знаков", combined)
        self.assertIn("пяти очень коротких абзацев", combined)
        self.assertNotIn("600–1000 знаков", combined)


if __name__ == "__main__":
    unittest.main()
