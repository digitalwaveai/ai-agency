import unittest

from leadpilot.outreach import OutreachGenerator


class OutreachTests(unittest.TestCase):
    def test_extracts_responses_api_text(self):
        payload = {
            "output": [
                {
                    "content": [
                        {"type": "output_text", "text": "Готовое сообщение"}
                    ]
                }
            ]
        }
        self.assertEqual(
            OutreachGenerator.extract_text(payload), "Готовое сообщение"
        )


if __name__ == "__main__":
    unittest.main()
