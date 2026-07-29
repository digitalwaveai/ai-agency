import json
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from leadpilot.models import Lead
from leadpilot.project_search_context import (
    EXCLUSION_SEPARATOR,
    _compose_search_target,
    install_project_search_context,
)
from leadpilot.search_quality import (
    _dedupe_key,
    _parse_payload,
    _quality,
    _query_plans,
    _split_target,
    install_search_quality,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class SearchQualityTests(unittest.TestCase):
    def test_project_offer_and_priorities_do_not_pollute_search_query(self):
        project = {
            "niche": "салоны красоты",
            "offer": "создание и продвижение сайтов",
            "priorities": "с активными соцсетями, но без онлайн-записи",
            "exclusions": "крупные сети",
        }

        target = _compose_search_target(
            project,
            "владельцы салонов красоты",
        )
        search_target, _, exclusions = target.partition(EXCLUSION_SEPARATOR)
        primary, secondary, priorities = _split_target(search_target)
        plans = _query_plans(primary, secondary, "Воронеж")

        self.assertEqual(priorities, project["priorities"])
        self.assertEqual(exclusions, project["exclusions"])
        self.assertNotIn(project["offer"], target)
        for _, query in plans:
            self.assertNotIn("активными соцсетями", query)
            self.assertNotIn("онлайн-записи", query)
            self.assertNotIn("создание", query)
            self.assertNotIn("продвижение", query)

    def test_screenshot_articles_posts_and_provider_pages_are_rejected(self):
        candidates = [
            Lead(
                name="✨ Почему салону красоты нужен сайт?",
                source_url="https://www.instagram.com/p/DbTdi86jvpS/",
                website="https://www.instagram.com/p/DbTdi86jvpS/",
                snippet="Салон красоты, Воронеж",
            ),
            Lead(
                name="Как салону красоты увеличить прибыль с помощью Авито",
                source_url="https://wahelp.ru/blog/articles/uvelichite-pribyl-salona-krasoty-cherez-avito/",
                website="https://wahelp.ru/blog/articles/uvelichite-pribyl-salona-krasoty-cherez-avito/",
                snippet="Салоны красоты, Воронеж",
            ),
            Lead(
                name="Продвижение сайта на региональную аудиторию",
                source_url="https://yagla.ru/blog/marketing/prodvijenie-sayta/",
                website="https://yagla.ru/blog/marketing/prodvijenie-sayta/",
                snippet="Продвижение салона красоты в Воронеже",
            ),
            Lead(
                name="Как открыть салон красоты в 2026 году",
                source_url="https://businessmens.ru/article/idei/kak-otkryt-salon-krasoty",
                website="https://businessmens.ru/article/idei/kak-otkryt-salon-krasoty",
                snippet="Салон красоты, Воронеж",
            ),
            Lead(
                name="Сервисы для мастеров: 15 популярных сайтов",
                source_url="https://petr-panda.ru/15-servisov-dlya-masterov/",
                website="https://petr-panda.ru/15-servisov-dlya-masterov/",
                snippet="Салоны и бьюти-мастера Воронежа",
            ),
            Lead(
                name="Как открыть салон красоты с нуля: пошаговый план",
                source_url="https://lorealprofessionnel.ru/blog/kak-otkryt-salon-krasoty/",
                website="https://lorealprofessionnel.ru/blog/kak-otkryt-salon-krasoty/",
                snippet="Салон красоты, Воронеж",
            ),
            Lead(
                name="Заказать фирменный логотип для салона",
                source_url="https://logo-monstr.ru/zakazat-logotip/",
                website="https://logo-monstr.ru/zakazat-logotip/",
                snippet="Дизайн для салона красоты в Воронеже",
            ),
        ]

        for lead in candidates:
            with self.subTest(name=lead.name):
                self.assertIsNone(
                    _quality(
                        lead,
                        "владельцы салонов красоты",
                        "салоны красоты",
                        "Воронеж",
                        "google",
                    )
                )

    def test_real_business_homepage_and_direct_profile_are_accepted(self):
        homepage = Lead(
            name="Аврора — салон красоты",
            source_url="https://aurora-vrn.ru/",
            website="https://aurora-vrn.ru/",
            phone="+7 900 000-00-00",
            address="Воронеж",
            snippet="Салон красоты и косметология",
        )
        profile = Lead(
            name="Аврора | Салон красоты | Воронеж",
            source_url="https://instagram.com/aurora_vrn/",
            website="https://instagram.com/aurora_vrn/",
            snippet="Студия красоты и косметология в Воронеже",
        )

        for lead in (homepage, profile):
            with self.subTest(name=lead.name):
                score = _quality(
                    lead,
                    "владельцы салонов красоты",
                    "салоны красоты",
                    "Воронеж",
                    "google",
                )
                self.assertIsNotNone(score)
                self.assertGreaterEqual(score, 70)

    def test_maps_parser_keeps_only_active_contactable_businesses(self):
        payload = {
            "local_results": [
                {
                    "title": "Рекламная карточка",
                    "sponsored": True,
                    "place_id": "ad-1",
                    "phone": "+7 900 000-00-01",
                },
                {
                    "title": "Закрытый салон",
                    "open_state": "Закрыто навсегда",
                    "place_id": "closed-1",
                    "phone": "+7 900 000-00-02",
                },
                {
                    "title": "Карточка без контакта",
                    "place_id": "no-contact-1",
                    "address": "Воронеж",
                },
                {
                    "title": "Аврора",
                    "type": "Салон красоты",
                    "place_id": "ChIJ-aurora",
                    "phone": "+7 900 000-00-03",
                    "website": "https://aurora-vrn.ru/",
                    "address": "Воронеж",
                },
            ]
        }

        leads = _parse_payload(
            payload,
            "салоны красоты Воронеж",
            "google_maps",
        )

        self.assertEqual([lead.name for lead in leads], ["Аврора"])
        self.assertEqual(leads[0].website, "https://aurora-vrn.ru/")
        self.assertTrue(
            leads[0].source_url.startswith(
                "https://www.google.com/maps/search/"
            )
        )

    def test_different_social_profiles_are_not_deduplicated(self):
        first = Lead(
            name="Аврора",
            source_url="https://instagram.com/aurora_vrn/",
            website="https://instagram.com/aurora_vrn/",
        )
        second = Lead(
            name="Лотос",
            source_url="https://instagram.com/lotos_vrn/",
            website="https://instagram.com/lotos_vrn/",
        )

        self.assertNotEqual(_dedupe_key(first), _dedupe_key(second))

    @patch("leadpilot.search_quality.urlopen")
    def test_installed_search_never_fills_results_with_articles(
        self,
        mocked_open,
    ):
        mocked_open.side_effect = [
            FakeResponse(
                {
                    "organic_results": [
                        {
                            "title": "Как открыть салон красоты",
                            "link": "https://example.com/article/salon",
                        }
                    ]
                }
            ),
            FakeResponse(
                {
                    "organic_results": [
                        {
                            "title": "Почему салону нужен сайт",
                            "link": "https://instagram.com/p/post-id/",
                            "snippet": "Салон красоты Воронеж",
                        },
                        {
                            "title": "Аврора — салон красоты",
                            "link": "https://aurora-vrn.ru/",
                            "snippet": "Салон красоты в Воронеже, +7 900 000-00-04",
                        },
                    ]
                }
            ),
        ]

        class DummySerpApi:
            endpoint = "https://serpapi.com/search.json"

            def __init__(self):
                self.api_key = "secret"
                self.demo_mode = False

            def search(self, target, region, limit=5):
                return []

            def _demo(self, query):
                return []

        class DummyBot:
            async def _search_and_reply(
                self,
                update,
                target,
                region,
                limit,
                *,
                project_id=None,
            ):
                return []

        install_search_quality(DummyBot, DummySerpApi)
        install_project_search_context(DummyBot, DummySerpApi)
        target = _compose_search_target(
            {
                "niche": "салоны красоты",
                "priorities": "активные соцсети без онлайн-записи",
                "exclusions": "крупные сети",
            },
            "владельцы салонов красоты",
        )
        leads = DummySerpApi().search(
            target,
            "Воронеж",
            5,
        )

        self.assertEqual([lead.name for lead in leads], ["Аврора — салон красоты"])
        self.assertEqual(mocked_open.call_count, 2)
        first_request = mocked_open.call_args_list[0].args[0]
        first_params = parse_qs(urlsplit(first_request.full_url).query)
        self.assertEqual(first_params["engine"], ["google_maps"])
        self.assertEqual(first_params["type"], ["search"])
        self.assertNotIn("онлайн-записи", first_params["q"][0])


if __name__ == "__main__":
    unittest.main()
