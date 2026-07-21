from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

from app.database import SessionLocal, init_db
from app.services.access_service import (
    AccessError,
    ensure_owner_access,
    find_user_by_identity,
    get_effective_access,
    grant_admin_by_owner,
    grant_beta_access,
    revoke_access,
    revoke_admin_by_owner,
)
from app.services.analytics_service import log_analytics_event
from app.services.plan_service import seed_default_plans
from app.services.payment_service import PaymentError
from app.services.pricing_service import (
    PricingError,
    format_owner_pricing_text,
    get_pricing_state,
    set_active_pricing_profile,
    set_profile_price,
)
from app.services.niche_profile_service import seed_niche_profiles
from app.services.subscription_service import register_identity
from app.services.telegram_stars_service import (
    PAID_DURATION_MONTHS,
    PAID_PLAN_CODES,
    admin_notification_needs_delivery,
    create_stars_invoice,
    duration_text,
    fail_stars_invoice,
    format_owner_stars_report,
    format_stars_catalog,
    list_stars_prices,
    mark_admin_notification_delivery,
    plan_name,
    process_stars_payment,
    stars_price,
    validate_stars_checkout,
)
from app.services.telegram_service import (
    TelegramServiceError,
    format_account_text,
    format_limits_text,
    parse_access_duration,
    register_telegram_account,
    user_leads_count,
)
from app.telegram_projects import (
    leadpilot_main_keyboard,
    router as project_router,
)


from app.telegram_search import router as search_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("beauty_telegram_bot")

router = Router()

BUTTON_SEARCH = "🔎 Найти клиентов"
BUTTON_LEADS = "📋 Мои лиды"
BUTTON_AUDIT = "💎 Анализ клиента"
BUTTON_MESSAGE = "✉️ Создать сообщение"
BUTTON_RADARS = "📡 Радары"
BUTTON_LIMITS = "📊 Лимиты"
BUTTON_PLANS = "⭐ Тарифы"
BUTTON_SUPPORT = "🛟 Поддержка"
BUTTON_SETTINGS = "⚙️ Настройки"


def _owner_pricing_keyboard(active_profile: str) -> InlineKeyboardMarkup:
    production_prefix = "✅ " if active_profile == "production" else ""
    test_prefix = "✅ " if active_profile == "test" else ""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{production_prefix}💳 Рабочие цены",
                    callback_data="owner_prices:production",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{test_prefix}🧪 Тестовые цены",
                    callback_data="owner_prices:test",
                )
            ],
        ]
    )


def _buy_plan_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ Стандарт",
                    callback_data="buy_plan:standard",
                ),
                InlineKeyboardButton(
                    text="🚀 Pro",
                    callback_data="buy_plan:pro",
                ),
            ]
        ]
    )


def _buy_duration_keyboard(
    plan_code: str,
    prices: dict[int, int],
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{duration_text(duration)} — {prices[duration]} ⭐",
                callback_data=f"buy_duration:{plan_code}:{duration}",
            )
        ]
        for duration in PAID_DURATION_MONTHS
    ]
    rows.append(
        [InlineKeyboardButton(text="← К тарифам", callback_data="buy_plans")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _buy_confirmation_keyboard(
    plan_code: str,
    duration_months: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Оплатить ⭐",
                    callback_data=f"buy_pay:{plan_code}:{duration_months}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="← Изменить срок",
                    callback_data=f"buy_plan:{plan_code}",
                )
            ],
        ]
    )


def main_keyboard() -> ReplyKeyboardMarkup:
    return leadpilot_main_keyboard()


def _owner_telegram_id() -> str | None:
    value = os.getenv("OWNER_TELEGRAM_ID") or os.getenv("ADMIN_TELEGRAM_ID")
    return value.strip() if value and value.strip() else None


def _admin_telegram_id() -> str | None:
    # Compatibility with the existing Telegram registration service.
    return _owner_telegram_id()


def _support_text() -> str:
    username = (os.getenv("TELEGRAM_SUPPORT_USERNAME") or "").strip()
    if username:
        if not username.startswith("@"):
            username = "@" + username
        return (
            "🛟 <b>Поддержка</b>\n\n"
            f"Напишите: <b>{username}</b>\n\n"
            "Не отправляйте токены, пароли и данные банковских карт."
        )
    return (
        "🛟 <b>Поддержка</b>\n\n"
        "Контакт поддержки пока настраивается.\n"
        "Не отправляйте токены, пароли и данные банковских карт."
    )


def _ensure_telegram_user(tg_user):
    db = SessionLocal()
    try:
        account = register_telegram_account(
            db,
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            admin_telegram_id=_admin_telegram_id(),
        )
        owner_id = _owner_telegram_id()
        if owner_id and str(tg_user.id) == owner_id:
            ensure_owner_access(db, user_id=account.user.id)
        return account
    finally:
        db.close()


def _ensure_account(message: Message):
    tg_user = message.from_user
    if tg_user is None:
        raise RuntimeError("Telegram-пользователь не определён")
    return _ensure_telegram_user(tg_user)


def _log_message_event(
    message: Message,
    *,
    event_name: str,
    command_name: str | None = None,
    status: str = "success",
    parameters: dict | None = None,
    error_message: str | None = None,
) -> None:
    tg_user = message.from_user
    if tg_user is None:
        return

    db = SessionLocal()
    try:
        user = find_user_by_identity(
            db,
            platform="telegram",
            external_user_id=tg_user.id,
        )
        access = get_effective_access(db, user.id) if user else None
        log_analytics_event(
            db,
            platform="telegram",
            event_name=event_name,
            user_id=user.id if user else None,
            external_user_id=tg_user.id,
            username=tg_user.username,
            plan_code=access.role if access and access.unlimited else None,
            command_name=command_name,
            parameters=parameters,
            status=status,
            error_message=error_message,
            session_id=str(message.chat.id),
        )
    except Exception:
        logger.exception("Не удалось сохранить событие аналитики")
    finally:
        db.close()


def _is_owner_admin(message: Message) -> bool:
    tg_user = message.from_user
    configured = _owner_telegram_id()
    return bool(tg_user and configured and str(tg_user.id) == configured)


def _owner_internal_user_id(message: Message) -> int:
    if not _is_owner_admin(message):
        raise AccessError("Команда доступна только владельцу")
    return _ensure_account(message).user.id


async def _send_placeholder(message: Message, title: str) -> None:
    _ensure_account(message)
    _log_message_event(
        message,
        event_name="menu_opened",
        parameters={"section": title},
    )
    await message.answer(
        f"{title}\n\n"
        "Раздел уже подготовлен в коммерческом ядре. "
        "Рабочий сценарий подключим следующим пакетом.",
        reply_markup=main_keyboard(),
    )


@router.pre_checkout_query()
async def confirm_stars_checkout(query: PreCheckoutQuery) -> None:
    try:
        account = _ensure_telegram_user(query.from_user)
        db = SessionLocal()
        try:
            decision = validate_stars_checkout(
                db,
                user_id=account.user.id,
                invoice_payload=query.invoice_payload,
                currency=query.currency,
                total_amount=query.total_amount,
            )
        finally:
            db.close()
    except Exception:
        logger.exception("Ошибка проверки платежа Stars")
        await query.answer(
            ok=False,
            error_message="Не удалось проверить счёт. Создайте новый и попробуйте ещё раз.",
        )
        return

    await query.answer(
        ok=decision.ok,
        error_message=decision.error_message,
    )


@router.message(F.successful_payment)
async def successful_stars_payment(message: Message) -> None:
    successful = message.successful_payment
    if successful is None:
        return
    try:
        account = _ensure_account(message)
        db = SessionLocal()
        try:
            receipt = process_stars_payment(
                db,
                user_id=account.user.id,
                invoice_payload=successful.invoice_payload,
                currency=successful.currency,
                total_amount=successful.total_amount,
                telegram_payment_charge_id=(
                    successful.telegram_payment_charge_id
                ),
                provider_payment_charge_id=(
                    successful.provider_payment_charge_id
                ),
            )
            owner_report = format_owner_stars_report(db, receipt)
            notify_owner = admin_notification_needs_delivery(
                db,
                payment_id=receipt.payment.id,
            )
            payment_id = receipt.payment.id
        finally:
            db.close()
    except Exception as exc:
        logger.exception("Оплата Stars получена, но активация завершилась ошибкой")
        _log_message_event(
            message,
            event_name="stars_payment_activation_failed",
            status="error",
            error_message=str(exc),
        )
        await message.answer(
            "Оплата получена, но подписка не активировалась автоматически. "
            "Не оплачивайте повторно — напишите в /paysupport."
        )
        return

    await message.answer(
        "✅ <b>Оплата подтверждена</b>\n\n"
        f"Тариф: <b>{receipt.plan.name}</b>\n"
        f"Срок: <b>{duration_text(receipt.payment.duration_months)}</b>\n"
        f"Сумма: <b>{receipt.payment.amount_minor} ⭐</b>\n"
        f"Активно до: <b>{receipt.subscription.ends_at:%d.%m.%Y %H:%M}</b>\n\n"
        "Лимиты уже активированы.",
        reply_markup=main_keyboard(),
    )
    _log_message_event(
        message,
        event_name="stars_payment_succeeded",
        parameters={
            "payment_id": payment_id,
            "plan_code": receipt.plan.code,
            "duration_months": receipt.payment.duration_months,
            "amount": receipt.payment.amount_minor,
            "duplicate": receipt.duplicate,
        },
    )

    owner_id = _owner_telegram_id()
    if owner_id and notify_owner:
        sent = False
        delivery_error = None
        try:
            await message.bot.send_message(chat_id=owner_id, text=owner_report)
            sent = True
        except Exception as exc:
            delivery_error = str(exc)
            logger.exception("Не удалось отправить владельцу отчёт об оплате")
        db = SessionLocal()
        try:
            mark_admin_notification_delivery(
                db,
                payment_id=payment_id,
                sent=sent,
                error_message=delivery_error,
            )
        finally:
            db.close()


@router.message(CommandStart())
async def command_start(message: Message) -> None:
    try:
        account = _ensure_account(message)
        _log_message_event(
            message,
            event_name="bot_started",
            command_name="start",
            parameters={"demo_created": account.demo_created},
        )
        text = (
            "✨ <b>LeadPilot AI</b>\n\n"
            "AI-система поиска клиентов для специалистов и агентств.\n\n"
            + format_account_text(account)
        )
        await message.answer(text, reply_markup=main_keyboard())
    except Exception as exc:
        logger.exception("Ошибка /start")
        _log_message_event(
            message,
            event_name="bot_start_failed",
            command_name="start",
            status="error",
            error_message=str(exc),
        )
        await message.answer(
            "Не удалось открыть аккаунт. Попробуйте ещё раз через минуту."
        )


@router.message(Command("menu"))
async def command_menu(message: Message) -> None:
    _ensure_account(message)
    _log_message_event(message, event_name="menu_opened", command_name="menu")
    await message.answer("Главное меню:", reply_markup=main_keyboard())


@router.message(Command("myid"))
async def command_myid(message: Message) -> None:
    tg_user = message.from_user
    if tg_user is None:
        return
    _ensure_account(message)
    await message.answer(
        "Ваш Telegram ID:\n"
        f"<code>{tg_user.id}</code>\n\n"
        "Этот ID используется для выдачи Beta Tester и подписки."
    )


@router.message(Command("plans"))
@router.message(F.text == BUTTON_PLANS)
async def show_plans(message: Message) -> None:
    _ensure_account(message)
    db = SessionLocal()
    try:
        text = format_stars_catalog(db)
    finally:
        db.close()
    _log_message_event(message, event_name="pricing_viewed", command_name="plans")
    await message.answer(text, reply_markup=_buy_plan_keyboard())


@router.callback_query(F.data == "buy_plans")
async def choose_purchase_plan(callback: CallbackQuery) -> None:
    _ensure_telegram_user(callback.from_user)
    db = SessionLocal()
    try:
        text = format_stars_catalog(db)
    finally:
        db.close()
    if callback.message is not None:
        await callback.message.edit_text(text, reply_markup=_buy_plan_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("buy_plan:"))
async def choose_purchase_duration(callback: CallbackQuery) -> None:
    plan_code = (callback.data or "").partition(":")[2]
    if plan_code not in PAID_PLAN_CODES:
        await callback.answer("Тариф не найден", show_alert=True)
        return
    _ensure_telegram_user(callback.from_user)
    try:
        db = SessionLocal()
        try:
            prices = list_stars_prices(db, plan_code=plan_code)
        finally:
            db.close()
    except PaymentError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    text = (
        f"⭐ <b>{plan_name(plan_code)}</b>\n\n"
        "Выберите срок доступа. Оплата разовая, без автопродления:"
    )
    if callback.message is not None:
        await callback.message.edit_text(
            text,
            reply_markup=_buy_duration_keyboard(plan_code, prices),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("buy_duration:"))
async def confirm_purchase(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or parts[1] not in PAID_PLAN_CODES:
        await callback.answer("Параметры покупки устарели", show_alert=True)
        return
    try:
        duration_months = int(parts[2])
    except ValueError:
        await callback.answer("Некорректный срок", show_alert=True)
        return
    if duration_months not in PAID_DURATION_MONTHS:
        await callback.answer("Некорректный срок", show_alert=True)
        return

    _ensure_telegram_user(callback.from_user)
    try:
        db = SessionLocal()
        try:
            amount = stars_price(
                db,
                plan_code=parts[1],
                duration_months=duration_months,
            )
        finally:
            db.close()
    except PaymentError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    text = (
        "🧾 <b>Проверьте заказ</b>\n\n"
        f"Тариф: <b>{plan_name(parts[1])}</b>\n"
        f"Срок: <b>{duration_text(duration_months)}</b>\n"
        f"К оплате: <b>{amount} ⭐</b>\n\n"
        "После оплаты тариф и лимиты активируются автоматически."
    )
    if callback.message is not None:
        await callback.message.edit_text(
            text,
            reply_markup=_buy_confirmation_keyboard(
                parts[1],
                duration_months,
            ),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("buy_pay:"))
async def send_stars_invoice(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or parts[1] not in PAID_PLAN_CODES:
        await callback.answer("Параметры покупки устарели", show_alert=True)
        return
    try:
        duration_months = int(parts[2])
    except ValueError:
        await callback.answer("Некорректный срок", show_alert=True)
        return
    if duration_months not in PAID_DURATION_MONTHS:
        await callback.answer("Некорректный срок", show_alert=True)
        return

    await callback.answer("Создаю счёт…")
    account = _ensure_telegram_user(callback.from_user)
    db = SessionLocal()
    try:
        invoice = create_stars_invoice(
            db,
            user_id=account.user.id,
            telegram_user_id=callback.from_user.id,
            username=callback.from_user.username,
            plan_code=parts[1],
            duration_months=duration_months,
        )
    except PaymentError as exc:
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=f"Не удалось создать счёт: {exc}",
        )
        return
    finally:
        db.close()

    try:
        await callback.bot.send_invoice(
            chat_id=callback.from_user.id,
            title=f"LeadPilot AI — {invoice.plan_name}",
            description=(
                f"Разовый доступ на {duration_text(invoice.duration_months)}. "
                "Без автопродления."
            ),
            payload=invoice.invoice_payload,
            provider_token="",
            currency="XTR",
            prices=[
                LabeledPrice(
                    label=(
                        f"{invoice.plan_name}, "
                        f"{duration_text(invoice.duration_months)}"
                    ),
                    amount=invoice.amount,
                )
            ],
            start_parameter=f"stars_{invoice.payment_id}",
            protect_content=True,
        )
    except Exception as exc:
        logger.exception("Telegram не создал счёт Stars")
        db = SessionLocal()
        try:
            fail_stars_invoice(
                db,
                invoice_payload=invoice.invoice_payload,
                error_message=str(exc),
            )
        finally:
            db.close()
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=(
                "Не удалось отправить счёт. Попробуйте снова через раздел "
                "«Тарифы» или напишите в /paysupport."
            ),
        )


@router.message(Command("limits"))
@router.message(F.text == BUTTON_LIMITS)
async def show_limits(message: Message) -> None:
    account = _ensure_account(message)
    db = SessionLocal()
    try:
        text = format_limits_text(db, account.user.id)
    finally:
        db.close()
    _log_message_event(message, event_name="limits_viewed", command_name="limits")
    await message.answer(text, reply_markup=main_keyboard())


@router.message(F.text == BUTTON_LEADS)
async def show_user_leads(message: Message) -> None:
    account = _ensure_account(message)
    db = SessionLocal()
    try:
        count = user_leads_count(db, account.user.id)
    finally:
        db.close()
    _log_message_event(
        message,
        event_name="user_leads_opened",
        parameters={"count": count},
    )
    await message.answer(
        "📋 <b>Мои лиды</b>\n\n"
        f"Сохранено: <b>{count}</b>\n\n"
        "Просмотр карточек подключим следующим пакетом.",
        reply_markup=main_keyboard(),
    )


@router.message(Command("paysupport"))
@router.message(Command("support"))
@router.message(F.text == BUTTON_SUPPORT)
async def show_support(message: Message) -> None:
    _ensure_account(message)
    _log_message_event(message, event_name="support_opened", command_name="support")
    await message.answer(_support_text(), reply_markup=main_keyboard())


@router.message(F.text == BUTTON_SETTINGS)
async def placeholder_settings(message: Message) -> None:
    await _send_placeholder(message, "⚙️ <b>Настройки</b>")


@router.message(F.text == BUTTON_SEARCH)
async def placeholder_search(message: Message) -> None:
    await _send_placeholder(message, "🔎 <b>Найти клиентов</b>")


@router.message(F.text == BUTTON_AUDIT)
async def placeholder_audit(message: Message) -> None:
    await _send_placeholder(message, "💎 <b>Анализ клиента</b>")


@router.message(F.text == BUTTON_MESSAGE)
async def placeholder_message(message: Message) -> None:
    await _send_placeholder(message, "✉️ <b>Создать сообщение</b>")


@router.message(F.text == BUTTON_RADARS)
async def placeholder_radars(message: Message) -> None:
    await _send_placeholder(message, "📡 <b>Мои радары</b>")


@router.message(Command("owner_prices"))
async def owner_prices(message: Message) -> None:
    if not _is_owner_admin(message):
        await message.answer("Команда доступна только владельцу.")
        return

    _owner_internal_user_id(message)
    db = SessionLocal()
    try:
        state = get_pricing_state(db)
        text = format_owner_pricing_text(db)
    finally:
        db.close()
    await message.answer(
        text,
        reply_markup=_owner_pricing_keyboard(state.active_profile),
    )


@router.callback_query(F.data.startswith("owner_prices:"))
async def owner_switch_prices(callback: CallbackQuery) -> None:
    configured_owner = _owner_telegram_id()
    if not configured_owner or str(callback.from_user.id) != configured_owner:
        await callback.answer("Доступно только владельцу", show_alert=True)
        return

    profile_code = (callback.data or "").partition(":")[2]
    db = SessionLocal()
    try:
        owner = register_identity(
            db,
            platform="telegram",
            external_user_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
            last_name=callback.from_user.last_name,
        )
        ensure_owner_access(db, user_id=owner.id)
        state = set_active_pricing_profile(
            db,
            owner_user_id=owner.id,
            profile_code=profile_code,
        )
        text = format_owner_pricing_text(db)
    except (PricingError, AccessError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    finally:
        db.close()

    if callback.message is not None:
        await callback.message.edit_text(
            text,
            reply_markup=_owner_pricing_keyboard(state.active_profile),
        )
    await callback.answer("Режим цен изменён")


@router.message(Command("owner_price_set"))
async def owner_set_price(message: Message) -> None:
    if not _is_owner_admin(message):
        await message.answer("Команда доступна только владельцу.")
        return

    parts = (message.text or "").split()
    if len(parts) != 6:
        await message.answer(
            "Формат:\n"
            "<code>/owner_price_set РЕЖИМ ТАРИФ МЕСЯЦЫ ВАЛЮТА СУММА</code>\n\n"
            "Примеры:\n"
            "<code>/owner_price_set production standard 1 RUB 990</code>\n"
            "<code>/owner_price_set production pro 1 XTR 2450</code>\n\n"
            "Для RUB сумма указывается в рублях, для XTR — в Stars."
        )
        return

    _, profile_code, plan_code, duration_text, currency, amount_text = parts
    try:
        duration_months = int(duration_text)
        display_amount = int(amount_text)
        if display_amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Месяцы и сумма должны быть положительными целыми числами.")
        return

    currency = currency.upper()
    amount_minor = display_amount * 100 if currency == "RUB" else display_amount
    try:
        owner_user_id = _owner_internal_user_id(message)
        db = SessionLocal()
        try:
            set_profile_price(
                db,
                owner_user_id=owner_user_id,
                profile_code=profile_code,
                plan_code=plan_code,
                duration_months=duration_months,
                currency=currency,
                amount_minor=amount_minor,
            )
            state = get_pricing_state(db)
            text = format_owner_pricing_text(db)
        finally:
            db.close()
    except (PricingError, AccessError) as exc:
        await message.answer(f"Ошибка: {exc}")
        return

    await message.answer(
        "✅ Цена сохранена\n\n" + text,
        reply_markup=_owner_pricing_keyboard(state.active_profile),
    )


@router.message(Command("admin_help"))
async def admin_help(message: Message) -> None:
    if not _is_owner_admin(message):
        await message.answer("Команда доступна только владельцу.")
        return
    await message.answer(
        "👑 <b>Команды владельца</b>\n\n"
        "<code>/owner_admin TELEGRAM_ID</code> — бессрочный Admin\n"
        "<code>/owner_admin TELEGRAM_ID 30</code> — Admin на 30 дней\n"
        "<code>/owner_revoke_admin TELEGRAM_ID</code> — снять Admin\n"
        "<code>/admin_beta TELEGRAM_ID 30</code> — Beta Tester\n"
        "<code>/admin_beta TELEGRAM_ID unlimited</code>\n"
        "<code>/admin_revoke_beta TELEGRAM_ID</code>\n"
        "<code>/admin_user TELEGRAM_ID</code> — проверить доступ\n\n"
        "<code>/owner_prices</code> — рабочие/тестовые цены\n"
        "<code>/owner_price_set ...</code> — изменить цену\n\n"
        "Владелец и Admin не расходуют лимиты. "
        "Только владелец может назначать и снимать Admin."
    )


@router.message(Command("owner_admin"))
async def owner_grant_admin(message: Message) -> None:
    if not _is_owner_admin(message):
        await message.answer("Команда доступна только владельцу.")
        return

    parts = (message.text or "").split()
    if len(parts) not in {2, 3}:
        await message.answer(
            "Формат:\n"
            "<code>/owner_admin TELEGRAM_ID</code>\n"
            "или\n"
            "<code>/owner_admin TELEGRAM_ID 30</code>"
        )
        return

    external_id = parts[1].strip()
    try:
        duration_days = None if len(parts) == 2 else parse_access_duration(parts[2])
        owner_user_id = _owner_internal_user_id(message)
        db = SessionLocal()
        try:
            target = register_identity(
                db,
                platform="telegram",
                external_user_id=external_id,
            )
            grant = grant_admin_by_owner(
                db,
                owner_user_id=owner_user_id,
                target_user_id=target.id,
                duration_days=duration_days,
                reason="Выдано владельцем через Telegram",
            )
            expires = (
                "бессрочно"
                if grant.ends_at is None
                else grant.ends_at.strftime("%d.%m.%Y %H:%M")
            )
        finally:
            db.close()
    except (AccessError, TelegramServiceError) as exc:
        await message.answer(f"Ошибка: {exc}")
        return

    _log_message_event(
        message,
        event_name="admin_access_granted",
        command_name="owner_admin",
        parameters={
            "target_telegram_id": external_id,
            "duration_days": duration_days,
        },
    )
    await message.answer(
        "✅ <b>Admin назначен</b>\n\n"
        f"Telegram ID: <code>{external_id}</code>\n"
        f"Лимиты: <b>отключены</b>\n"
        f"Действует до: <b>{expires}</b>"
    )


@router.message(Command("owner_revoke_admin"))
async def owner_revoke_admin(message: Message) -> None:
    if not _is_owner_admin(message):
        await message.answer("Команда доступна только владельцу.")
        return

    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer(
            "Формат:\n"
            "<code>/owner_revoke_admin TELEGRAM_ID</code>"
        )
        return

    external_id = parts[1].strip()
    try:
        owner_user_id = _owner_internal_user_id(message)
        db = SessionLocal()
        try:
            target = find_user_by_identity(
                db,
                platform="telegram",
                external_user_id=external_id,
            )
            count = 0 if target is None else revoke_admin_by_owner(
                db,
                owner_user_id=owner_user_id,
                target_user_id=target.id,
                reason="Снято владельцем через Telegram",
            )
        finally:
            db.close()
    except AccessError as exc:
        await message.answer(f"Ошибка: {exc}")
        return

    _log_message_event(
        message,
        event_name="admin_access_revoked",
        command_name="owner_revoke_admin",
        parameters={"target_telegram_id": external_id, "revoked": count},
    )
    await message.answer(f"Снято активных Admin-доступов: <b>{count}</b>")


@router.message(Command("admin_beta"))
async def admin_grant_beta(message: Message) -> None:
    if not _is_owner_admin(message):
        await message.answer("Команда доступна только владельцу.")
        return

    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer(
            "Формат:\n"
            "<code>/admin_beta TELEGRAM_ID ДНИ</code>\n"
            "или\n"
            "<code>/admin_beta TELEGRAM_ID unlimited</code>"
        )
        return

    external_id = parts[1].strip()
    try:
        duration_days = parse_access_duration(parts[2])
    except TelegramServiceError as exc:
        await message.answer(f"Ошибка: {exc}")
        return

    db = SessionLocal()
    try:
        target = register_identity(
            db,
            platform="telegram",
            external_user_id=external_id,
        )
        grant = grant_beta_access(
            db,
            user_id=target.id,
            duration_days=duration_days,
            reason="Выдано владельцем через Telegram",
        )
        expires = (
            "бессрочно"
            if grant.ends_at is None
            else grant.ends_at.strftime("%d.%m.%Y %H:%M")
        )
    finally:
        db.close()

    _log_message_event(
        message,
        event_name="beta_access_granted",
        command_name="admin_beta",
        parameters={
            "target_telegram_id": external_id,
            "duration_days": duration_days,
        },
    )
    await message.answer(
        "✅ Beta Tester выдан\n\n"
        f"Telegram ID: <code>{external_id}</code>\n"
        f"Действует до: <b>{expires}</b>"
    )


@router.message(Command("admin_revoke_beta"))
async def admin_revoke_beta(message: Message) -> None:
    if not _is_owner_admin(message):
        await message.answer("Команда доступна только владельцу.")
        return

    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer(
            "Формат:\n"
            "<code>/admin_revoke_beta TELEGRAM_ID</code>"
        )
        return

    external_id = parts[1].strip()
    db = SessionLocal()
    try:
        target = find_user_by_identity(
            db,
            platform="telegram",
            external_user_id=external_id,
        )
        if target is None:
            count = 0
        else:
            count = revoke_access(
                db,
                user_id=target.id,
                role="beta_tester",
                reason="Отозвано владельцем через Telegram",
            )
    finally:
        db.close()

    _log_message_event(
        message,
        event_name="beta_access_revoked",
        command_name="admin_revoke_beta",
        parameters={"target_telegram_id": external_id, "revoked": count},
    )
    await message.answer(f"Отозвано активных Beta-доступов: <b>{count}</b>")


@router.message(Command("admin_user"))
async def admin_show_user(message: Message) -> None:
    if not _is_owner_admin(message):
        await message.answer("Команда доступна только владельцу.")
        return

    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer(
            "Формат:\n"
            "<code>/admin_user TELEGRAM_ID</code>"
        )
        return

    external_id = parts[1].strip()
    db = SessionLocal()
    try:
        target = find_user_by_identity(
            db,
            platform="telegram",
            external_user_id=external_id,
        )
        if target is None:
            text = "Пользователь не найден."
        else:
            state = get_effective_access(db, target.id)
            expires = (
                "бессрочно"
                if state.ends_at is None and state.unlimited
                else (
                    state.ends_at.strftime("%d.%m.%Y %H:%M")
                    if state.ends_at
                    else "нет"
                )
            )
            text = (
                "👤 <b>Пользователь</b>\n\n"
                f"Telegram ID: <code>{external_id}</code>\n"
                f"Внутренний ID: <b>{target.id}</b>\n"
                f"Роль: <b>{state.role}</b>\n"
                f"Безлимит: <b>{'да' if state.unlimited else 'нет'}</b>\n"
                f"Действует до: <b>{expires}</b>"
            )
    finally:
        db.close()

    await message.answer(text)


async def set_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Открыть LeadPilot AI"),
            BotCommand(command="menu", description="Главное меню"),
            BotCommand(command="plans", description="Тарифы"),
            BotCommand(command="limits", description="Мои лимиты"),
            BotCommand(command="paysupport", description="Поддержка по оплате"),
            BotCommand(command="myid", description="Показать Telegram ID"),
            BotCommand(command="support", description="Поддержка"),
            BotCommand(command="new_project", description="Создать проект"),
            BotCommand(command="projects", description="Мои проекты"),
        ]
    )


async def main() -> None:
    load_dotenv(override=True)
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token or ":" not in token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN отсутствует или имеет неверный формат"
        )

    init_db()
    db = SessionLocal()
    try:
        seed_default_plans(db)
        seed_niche_profiles(db)
        owner_id = _owner_telegram_id()
        if owner_id:
            owner_user = register_identity(
                db,
                platform="telegram",
                external_user_id=owner_id,
                display_name="LeadPilot AI Owner",
            )
            ensure_owner_access(db, user_id=owner_user.id)
        else:
            logger.warning(
                "OWNER_TELEGRAM_ID не задан; используется только обычный доступ"
            )
    finally:
        db.close()

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(search_router)
    dispatcher.include_router(project_router)
    dispatcher.include_router(router)

    await bot.delete_webhook(drop_pending_updates=False)
    await set_bot_commands(bot)

    me = await bot.get_me()
    logger.info("Telegram-бот запущен: @%s", me.username)

    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
