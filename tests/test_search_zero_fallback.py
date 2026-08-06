import unittest

from leadpilot.search_zero_fallback import (
    _fallback_targets,
    install_zero_result_fallback,
)


class FakeSearchClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.last_search_diagnostics = {}

    def search(self, target, region, limit=5):
        self.calls.append((target, region, limit))
        leads, diagnostics = self.responses.pop(0)
        self.last_search_diagnostics = diagnostics
        return leads


class SearchZeroFallbackTests(unittest.TestCase):
    def test_removes_priorities_before_project_niche(self):
        target = (
            "владельцы салонов ||| бьюти-бизнес ||+ активный Instagram "
            "||- франшизы, курсы"
        )
        self.assertEqual(
            _fallback_targets(target),
            [
                "владельцы салонов ||| бьюти-бизнес ||- франшизы, курсы",
                "владельцы салонов ||- франшизы, курсы",
            ],
        )

    def test_retries_and_returns_results_from_relaxed_target(self):
        class Client(FakeSearchClient):
            pass

        original_search = Client.search
        install_zero_result_fallback(Client)
        client = Client(
            [
                ([], {"attempted_plans": 2, "failed_plans": 0, "accepted": 0}),
                (["lead"], {"attempted_plans": 1, "failed_plans": 0, "accepted": 1}),
            ]
        )

        result = client.search(
            "салоны ||| бьюти ||+ активные соцсети ||- курсы",
            "Воронеж",
            5,
        )

        self.assertEqual(result, ["lead"])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(
            client.calls[1][0],
            "салоны ||| бьюти ||- курсы",
        )
        self.assertTrue(client.last_search_diagnostics["fallback_used"])
        self.assertEqual(client.last_search_diagnostics["attempted_plans"], 3)
        Client.search = original_search

    def test_does_not_retry_when_serpapi_plans_all_failed(self):
        class Client(FakeSearchClient):
            pass

        install_zero_result_fallback(Client)
        client = Client(
            [([], {"attempted_plans": 2, "failed_plans": 2, "accepted": 0})]
        )

        result = client.search(
            "салоны ||| бьюти ||+ активные соцсети",
            "Москва",
            5,
        )

        self.assertEqual(result, [])
        self.assertEqual(len(client.calls), 1)

    def test_simple_target_is_not_broadened_without_safe_metadata(self):
        self.assertEqual(_fallback_targets("салоны красоты"), [])


if __name__ == "__main__":
    unittest.main()
