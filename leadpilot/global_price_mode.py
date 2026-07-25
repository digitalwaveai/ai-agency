from __future__ import annotations

from typing import Any

from telegram import LabeledPrice, Update
from telegram.ext import ContextTypes

from .bot import (
    LIVE_TARIFFS_TEXT,
    MENU,
    TEST_TARIFFS_TEXT,
    _owner_price_mode_keyboard,
    _star_payment_keyboard,
    _star_tariffs,
)


GLOBAL_TEST_TARIFFS_TEXT = TEST_TARIFFS_TEXT.replace(
    "Этот режим видите только вы. Для остальных пользователей всегда "
    "действуют реальные цены.",
    "Тестовый режим включён владельцем и действует для всех пользователей.",
)


def install_global_price_mode(bot_class: type[Any]) -> None:
    """Make the owner's selected price mode global while keeping control owner-only."""
    if getattr(bot_class, "_global_price_mode_installed", False):
        return

    def price_mode_for(self: Any, user_id: int) -> str:
        del user_id
        owner_id = self.settings.owner_telegram_id
        if owner_id is None:
            return "live"
        return self.db.get_price_mode(owner_id)

    def payment_mode_allowed(self: Any, user_id: int, mode: str) -> bool:
        del user_id
        return mode in {"live", "test"} and mode == self.price_mode_for(0)

    async def show_plans(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        self.ensure_account(update)
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return

        mode = self.price_mode_for(user.id)
        text = GLOBAL_TEST_TARIFFS_TEXT if mode == "test" else LIVE_TARIFFS_TEXT
        await message.reply_text(text, reply_markup=MENU)
        await message.reply_text(
            "Выберите тариф и срок для оплаты Telegram Stars:",
            reply_markup=_star_payment_keyboard(mode),
        )

    async def price_mode(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        self.ensure_account(update)
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        if not self.is_owner(update):
            await message.reply_text("⛔ Команда доступна только владельцу.", reply_markup=MENU)
            return

        aliases = {
            "test": "test",
            "тест": "test",
            "тестовые": "test",
            "live": "live",
            "real": "live",
            "реальные": "live",
        }
        requested = context.args[0].lower() if context.args else ""
        if requested:
            mode = aliases.get(requested)
            if mode is None:
                await message.reply_text(
                    "Формат команды:\n"
                    "/price_mode test — тестовые цены для всех\n"
                    "/price_mode live — реальные цены для всех",
                    reply_markup=MENU,
                )
                return
            if not self.db.set_owner_price_mode(user.id, mode):
                await message.reply_text("Не удалось изменить режим цен.", reply_markup=MENU)
                return
            label = "тестовые" if mode == "test" else "реальные"
            await message.reply_text(
                "✅ Общий режим цен переключён\n\n"
                f"Теперь для всех пользователей действуют {label} цены.",
                reply_markup=MENU,
            )
            return

        mode = self.db.get_price_mode(user.id)
        label = "тестовые" if mode == "test" else "реальные"
        await message.reply_text(
            "💳 Общий режим цен\n\n"
            f"Сейчас для всех пользователей включены: {label} цены.\n"
            "Выберите режим:",
            reply_markup=_owner_price_mode_keyboard(mode),
        )

    async def switch_price_mode(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        query = update.callback_query
        owner_id = self.settings.owner_telegram_id
        if query is None or query.from_user is None:
            return
        if owner_id is None or query.from_user.id != owner_id:
            await query.answer("Доступно только владельцу.", show_alert=True)
            return

        await query.answer()
        mode = (query.data or "").partition(":")[2]
        if mode not in {"test", "live"}:
            return

        self.db.ensure_account(
            query.from_user.id,
            username=query.from_user.username or "",
            first_name=query.from_user.first_name or "",
        )
        self.db.ensure_owner(query.from_user.id)
        if not self.db.set_owner_price_mode(query.from_user.id, mode):
            if query.message is not None:
                await query.message.reply_text(
                    "Не удалось изменить режим цен.",
                    reply_markup=MENU,
                )
            return

        label = "тестовые" if mode == "test" else "реальные"
        if query.message is not None:
            await query.message.edit_text(
                "✅ Общий режим цен переключён\n\n"
                f"Теперь для всех пользователей действуют {label} цены.",
                reply_markup=_owner_price_mode_keyboard(mode),
            )

    async def select_star_payment(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        query = update.callback_query
        if query is None or query.from_user is None or query.message is None:
            return
        await query.answer()

        self.db.ensure_account(
            query.from_user.id,
            username=query.from_user.username or "",
            first_name=query.from_user.first_name or "",
        )

        try:
            _, plan_code, months_raw, mode = (query.data or "").split(":")
            months = int(months_raw)
            if mode not in {"live", "test"}:
                raise ValueError
            if mode != self.price_mode_for(query.from_user.id):
                raise RuntimeError
            plan_name, stars = _star_tariffs(mode)[(plan_code, months)]
        except RuntimeError:
            await query.message.reply_text(
                "Режим цен уже изменён. Откройте «⭐ Тарифы» ещё раз.",
                reply_markup=MENU,
            )
            return
        except (ValueError, KeyError):
            await query.message.reply_text(
                "Не удалось определить тариф. Откройте «⭐ Тарифы» ещё раз.",
                reply_markup=MENU,
            )
            return

        payload = f"leadpilot|{plan_code}|{months}|{query.from_user.id}|{mode}"
        await context.bot.send_invoice(
            chat_id=query.message.chat_id,
            title=f"LeadPilot {plan_name} — {months} мес.",
            description=(
                f"Доступ к тарифу {plan_name} на {months} мес. "
                "Разовая оплата без автопродления."
            ),
            payload=payload,
            currency="XTR",
            prices=[LabeledPrice(f"{plan_name}, {months} мес.", stars)],
        )

    bot_class.price_mode_for = price_mode_for
    bot_class.payment_mode_allowed = payment_mode_allowed
    bot_class.show_plans = show_plans
    bot_class.price_mode = price_mode
    bot_class.switch_price_mode = switch_price_mode
    bot_class.select_star_payment = select_star_payment
    bot_class._global_price_mode_installed = True
