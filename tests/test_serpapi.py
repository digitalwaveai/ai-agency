import unittest

from leadpilot.serpapi import SerpApiClient


class SerpApiTests(unittest.TestCase):
    def test_parses_google_maps_results(self):
        payload = {
            "local_results": [
                {
                    "title": "Тестовая компания",
                    "website": "https://example.com",
                    "phone": "+7 999 000-00-00",
                    "address": "Казань",
                    "place_id": "place-1",
                    "rating": 4.8,
                    "reviews": 15,
                    "type": "Компания",
                }
            ]
        }
        leads = SerpApiClient.parse_results(payload, "компании Казань")
        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].name, "Тестовая компания")
        self.assertEqual(leads[0].score, 100)


if __name__ == "__main__":
    unittest.main()
