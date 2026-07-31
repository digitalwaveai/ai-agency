import json
import unittest
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from leadpilot.search_reliability import install_resilient_search


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class FakeClient:
    endpoint = "https://serpapi.test/search.json"

    def __init__(self):
        self.api_key = "test-key"
        self.demo_mode = False

    def search(self, target, region, limit=5):
        raise AssertionError("Original search must be replaced")

    @staticmethod
    def _demo(query):
        return []


install_resilient_search(FakeClient)


def organic_business_payload():
    return {
        "organic_results": [
            {
                "title": "Салон красоты Лаванда — официальный сайт",
                "link": "https://lavanda-vrn.ru/",
                "snippet": (
                    "Салон красоты в Воронеже. "
                    "Запись по телефону +7 473 000-00-00."
                ),
            }
        ]
    }


class ResilientSearchTests(unittest.TestCase):
    def test_serpapi_error_in_maps_falls_back_to_google(self):
        client = FakeClient()
        responses = [
            FakeResponse({"error": "Google Maps hasn't returned any results"}),
            FakeResponse(organic_business_payload()),
        ]

        with patch(
            "leadpilot.search_reliability.urlopen",
            side_effect=responses,
        ):
            leads = client.search("салон красоты", "Воронеж", 5)

        self.assertEqual(len(leads), 1)
        self.assertEqual(leads[0].website, "https://lavanda-vrn.ru/")

    def test_network_failure_in_one_plan_does_not_abort_search(self):
        client = FakeClient()
        responses = [
            URLError("temporary failure"),
            URLError("temporary failure"),
            FakeResponse(organic_business_payload()),
        ]

        with patch(
            "leadpilot.search_reliability.urlopen",
            side_effect=responses,
        ):
            leads = client.search("салон красоты", "Воронеж", 5)

        self.assertEqual(len(leads), 1)

    def test_article_is_rejected_and_direct_business_is_kept(self):
        client = FakeClient()
        payload = {
            "organic_results": [
                {
                    "title": "Топ-10 салонов красоты Воронежа",
                    "link": "https://vc.ru/services/beauty-rating",
                    "snippet": "Рейтинг лучших салонов красоты Воронежа.",
                },
                organic_business_payload()["organic_results"][0],
            ]
        }
        responses = [
            FakeResponse({"error": "no results"}),
            FakeResponse(payload),
        ]

        with patch(
            "leadpilot.search_reliability.urlopen",
            side_effect=responses,
        ):
            leads = client.search("салон красоты", "Воронеж", 5)

        self.assertEqual(len(leads), 1)
        self.assertNotIn("vc.ru", leads[0].website)

    def test_priorities_are_used_for_scoring_not_sent_to_serpapi(self):
        client = FakeClient()
        requested_urls = []
        maps_payload = {
            "local_results": [
                {
                    "title": "Лаванда",
                    "type": "Салон красоты",
                    "website": "https://lavanda-vrn.ru/",
                    "phone": "+7 473 000-00-00",
                    "address": "Воронеж, ул. Ленина, 1",
                    "place_id": "ChIJtest",
                }
            ]
        }

        def fake_urlopen(request, timeout=30):
            del timeout
            requested_urls.append(request.full_url)
            return FakeResponse(maps_payload)

        with patch(
            "leadpilot.search_reliability.urlopen",
            side_effect=fake_urlopen,
        ):
            leads = client.search(
                "салоны красоты ||+ активные соцсети",
                "Воронеж",
                1,
            )

        self.assertEqual(len(leads), 1)
        query = parse_qs(urlparse(requested_urls[0]).query)["q"][0]
        self.assertNotIn("активные соцсети", query)


if __name__ == "__main__":
    unittest.main()
