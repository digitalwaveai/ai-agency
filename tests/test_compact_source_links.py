import unittest

from leadpilot.compact_source_links import (
    _compact_plain_source_text,
    _format_lead_html,
    _source_label,
    _source_link,
)
from leadpilot.models import Lead


class CompactSourceLinksTests(unittest.TestCase):
    def test_google_maps_source_uses_short_clickable_label(self):
        url = (
            "https://www.google.com/maps/search/?api=1&query=%D0%A6%D0%B5%D0%BD%D1%82%D1%80"
            "&query_place_id=ChIJExample"
        )
        lead = Lead(
            id=7,
            name="Центр",
            source_url=url,
            phone="+7 900 000-00-00",
            score=88,
        )

        rendered = _format_lead_html(lead)

        self.assertIn("Источник: <a href=", rendered)
        self.assertIn(">Google Карты</a>", rendered)
        self.assertNotIn("query_place_id=ChIJExample\n", rendered)

    def test_plain_bot_output_is_converted_to_compact_html(self):
        url = (
            "https://www.google.com/maps/search/?api=1&query=%D0%A3%D0%BC%D0%BD%D0%B0%D1%8F"
            "&query_place_id=ChIJVeryLongExample"
        )
        text = (
            "ID 57 · Умная мойка\n"
            "Контакт: +7 499 490-04-35\n"
            f"Источник: {url}\n\n"
            "ID 58 · Следующий лид"
        )

        rendered, changed = _compact_plain_source_text(text)

        self.assertTrue(changed)
        self.assertIn("Источник: <a href=", rendered)
        self.assertIn(">Google Карты</a>", rendered)
        self.assertNotIn(f"Источник: {url}", rendered)
        self.assertIn("ID 58 · Следующий лид", rendered)

    def test_plain_output_escapes_other_lines_before_html_mode(self):
        text = (
            "ID 1 · A & B <studio>\n"
            "Источник: https://www.google.com/maps/place/example"
        )

        rendered, changed = _compact_plain_source_text(text)

        self.assertTrue(changed)
        self.assertIn("A &amp; B &lt;studio&gt;", rendered)
        self.assertIn(">Google Карты</a>", rendered)

    def test_text_without_source_url_is_untouched(self):
        text = "Обычное сообщение A & B"

        rendered, changed = _compact_plain_source_text(text)

        self.assertFalse(changed)
        self.assertEqual(rendered, text)

    def test_html_special_characters_are_escaped(self):
        lead = Lead(
            id=1,
            name="A & B <studio>",
            source_url="https://example.com/?a=1&b=2",
            score=50,
        )

        rendered = _format_lead_html(lead)

        self.assertIn("A &amp; B &lt;studio&gt;", rendered)
        self.assertIn("a=1&amp;b=2", rendered)

    def test_source_labels_for_common_platforms(self):
        self.assertEqual(
            _source_label("https://www.google.com/maps/place/example"),
            "Google Карты",
        )
        self.assertEqual(_source_label("https://vk.com/example"), "ВКонтакте")
        self.assertEqual(_source_label("https://t.me/example"), "Telegram")
        self.assertEqual(
            _source_label("https://www.instagram.com/example"),
            "Instagram",
        )

    def test_invalid_source_is_not_made_clickable(self):
        self.assertEqual(_source_link("not-a-url"), "Открыть источник")


if __name__ == "__main__":
    unittest.main()
