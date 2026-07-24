import unittest
from pathlib import Path


BOT_SOURCE = (
    Path(__file__).resolve().parents[1] / "leadpilot" / "bot.py"
).read_text(encoding="utf-8")


class BotInterfaceTests(unittest.TestCase):
    def test_full_menu_is_restored(self):
        expected_buttons = {
            "➕ Новый проект",
            "📁 Мои проекты",
            "🔎 Найти клиентов",
            "📋 Мои лиды",
            "📈 Воронка",
            "📤 Экспорт лидов",
            "📊 Аналитика лидов",
            "💎 Анализ клиента",
            "✉️ Создать сообщение",
            "📡 Радары",
            "📊 Лимиты",
            "⭐ Тарифы",
            "⚙️ Настройки",
            "🛟 Поддержка",
        }
        for button in expected_buttons:
            self.assertIn(button, BOT_SOURCE)

    def test_current_tariff_prices_are_present(self):
        self.assertIn("990 ₽", BOT_SOURCE)
        self.assertIn("24 990 ₽", BOT_SOURCE)
        self.assertIn("7 дней бесплатно", BOT_SOURCE)

    def test_correct_leadpilot_greeting_is_present(self):
        self.assertIn("✨ LeadPilot AI", BOT_SOURCE)
        self.assertIn(
            "AI-система поиска клиентов для специалистов и агентств.",
            BOT_SOURCE,
        )


if __name__ == "__main__":
    unittest.main()
