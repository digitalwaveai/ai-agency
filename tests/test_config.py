import os
import unittest
from unittest.mock import patch

from leadpilot.config import Settings


class SettingsTests(unittest.TestCase):
    def test_reads_railway_environment(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "123:test",
            "SERPAPI_KEY": "serp-key",
            "OPENAI_API_KEY": "openai-key",
            "DATABASE_URL": "postgresql://db",
            "OWNER_TELEGRAM_ID": "12345",
            "DEMO_MODE": "false",
            "SEARCH_PROVIDER": "serpapi",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.owner_telegram_id, 12345)
        self.assertFalse(settings.demo_mode)
        self.assertEqual(settings.search_provider, "serpapi")


if __name__ == "__main__":
    unittest.main()
