import unittest
from pathlib import Path

BOT_SOURCE = (Path(__file__).resolve().parents[1] / "leadpilot" / "bot.py").read_text(
    encoding="utf-8"
)


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

    def test_menu_buttons_interrupt_unfinished_dialogs(self):
        self.assertIn("USER_INPUT_FILTER", BOT_SOURCE)
        self.assertIn("~filters.Regex(MENU_BUTTON_PATTERN)", BOT_SOURCE)
        self.assertIn("self.navigate_menu", BOT_SOURCE)
        self.assertIn("allow_reentry=True", BOT_SOURCE)

    def test_telegram_stars_payment_is_complete(self):
        self.assertIn('currency="XTR"', BOT_SOURCE)
        self.assertIn("PreCheckoutQueryHandler", BOT_SOURCE)
        self.assertIn("filters.SUCCESSFUL_PAYMENT", BOT_SOURCE)
        self.assertIn("record_star_payment", BOT_SOURCE)

    def test_correct_leadpilot_greeting_is_present(self):
        self.assertIn("✨ LeadPilot AI", BOT_SOURCE)
        self.assertIn(
            "AI-система поиска клиентов для специалистов и агентств.",
            BOT_SOURCE,
        )

    def test_project_flow_uses_categories_and_six_questions(self):
        for label in (
            "Beauty и здоровье",
            "Финансы и образование",
            "Маркетинг и продажи",
            "AI и автоматизация",
            "Разработка и IT",
            "Дизайн и визуал",
            "Контент и видео",
            "Маркетплейсы и e-commerce",
            "Образование и консалтинг",
            "Бизнес-услуги",
            "Локальный бизнес",
            "Недвижимость и строительство",
            "Своя ниша",
        ):
            self.assertIn(label, BOT_SOURCE)
        self.assertIn("1/6. Как назвать проект?", BOT_SOURCE)
        self.assertIn("6/6. Какую главную проблему", BOT_SOURCE)
        self.assertIn("Клиенты пока не искались", BOT_SOURCE)
        self.assertIn("PROJECT_CATEGORY_EXAMPLES", BOT_SOURCE)
        self.assertIn("AI-продажи в недвижимости", BOT_SOURCE)

    def test_search_starts_with_a_project_and_three_questions(self):
        self.assertIn("Из какого проекта найти клиентов?", BOT_SOURCE)
        self.assertIn("1/3. Каких клиентов ищем сейчас?", BOT_SOURCE)
        self.assertIn("2/3. В каком городе или регионе искать?", BOT_SOURCE)
        self.assertIn("3/3. Сколько клиентов показать?", BOT_SOURCE)
        self.assertIn("search_project:", BOT_SOURCE)

    def test_owner_only_test_prices_are_present(self):
        self.assertIn('CommandHandler("price_mode", self.price_mode)', BOT_SOURCE)
        self.assertIn("TEST_STAR_TARIFFS", BOT_SOURCE)
        self.assertIn('("standard", 12): ("Стандарт", 4)', BOT_SOURCE)
        self.assertIn('("pro", 12): ("Pro", 10)', BOT_SOURCE)
        self.assertIn("/price_mode test — тестовые цены", BOT_SOURCE)
        self.assertIn("/price_mode live — реальные цены", BOT_SOURCE)
        self.assertIn("Доступно только владельцу.", BOT_SOURCE)

    def test_four_roles_and_management_boundaries_are_present(self):
        for role in ("owner", "admin", "beta_tester", "user"):
            self.assertIn(f'"{role}"', BOT_SOURCE)
        self.assertIn("managed_by", BOT_SOURCE)
        self.assertIn("owner_revoke_admin", BOT_SOURCE)
        self.assertIn("admin_grant_beta", BOT_SOURCE)


if __name__ == "__main__":
    unittest.main()
