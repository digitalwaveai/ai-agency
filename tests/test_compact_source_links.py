import unittest

from leadpilot.compact_source_links import (
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
