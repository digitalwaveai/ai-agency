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
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

from app.database import SessionLocal, init_db
from app.services.access_service import (
    find_user_by_identity,
    get_effective_access,
    grant_beta_access,
    revoke_access,
)
from app.services.analytics_service import log_analytics_event
from app.services.plan_service import seed_default_plans
from app.services.subscription_service import register_identity
from app.services.telegram_service import (
    TelegramServiceError,
    format_account_text,
    format_limits_text,
    format_plan_catalog,
    parse_access_duration,
    register_telegram_account,
    user_leads_count,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("beauty_telegram_bot")

router = Router()

BUTTON_SEARCH = "🔎 Найти лидов"
BUTTON_LEADS = "📋 Мои лиды"
BUTTON_AUDIT = "💎 Паспорт лида"
BUTTON_MESSAGE = "✉️ Создать сообщение"
BUTTON_RADARS = "📡 Мои радары"
BUTTON_LIMITS = "📊 Мои лимиты"
BUTTON_PLANS = "⭐ Тарифы"
BUTTON_SUPPORT = "🛟 Поддержка"


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BUTTON_SEARCH), KeyboardButton(text=BUTTON_LEADS)],
            [KeyboardButton(text=BUTTON_AUDIT), KeyboardButton(text=BUTTON_MESSAGE)],
            [KeyboardButton(text=BUTTON_RADARS), KeyboardButton(text=BUTTON_LIMITS)],
            [KeyboardButton(text=BUTTON_PLANS), KeyboardButton(text=BUTTON_SUPPORT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def _admin_telegram_id() -> str | None:
    value = os.getenv("ADMIN_TELEGRAM_ID")
    return value.strip() if value and value.strip() else None


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


def _ensure_account(message: Message):
    tg_user = message.from_user
    if tg_user is None:
        raise RuntimeError("Telegram-пользователь не определён")

    db = SessionLocal()
    try:
        return register_telegram_account(
            db,
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            admin_telegram_id=_admin_telegram_id(),
        )
    finally:
        db.close()


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
    configured = _admin_telegram_id()
    return bool(tg_user and configured and str(tg_user.id) == configured)


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
            "✨ <b>Beauty Lead Finder</b>\n\n"
            "Поиск и анализ потенциальных клиентов для beauty-бизнеса.\n\n"
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
        text = format_plan_catalog(db)
    finally:
        db.close()
    _log_message_event(message, event_name="pricing_viewed", command_name="plans")
    await message.answer(text, reply_markup=main_keyboard())


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


@router.message(Command("support"))
@router.message(F.text == BUTTON_SUPPORT)
async def show_support(message: Message) -> None:
    _ensure_account(message)
    _log_message_event(message, event_name="support_opened", command_name="support")
    await message.answer(_support_text(), reply_markup=main_keyboard())


@router.message(F.text == BUTTON_SEARCH)
async def placeholder_search(message: Message) -> None:
    await _send_placeholder(message, "🔎 <b>Найти лидов</b>")


@router.message(F.text == BUTTON_AUDIT)
async def placeholder_audit(message: Message) -> None:
    await _send_placeholder(message, "💎 <b>Паспорт лида</b>")


@router.message(F.text == BUTTON_MESSAGE)
async def placeholder_message(message: Message) -> None:
    await _send_placeholder(message, "✉️ <b>Создать сообщение</b>")


@router.message(F.text == BUTTON_RADARS)
async def placeholder_radars(message: Message) -> None:
    await _send_placeholder(message, "📡 <b>Мои радары</b>")


@router.message(Command("admin_help"))
async def admin_help(message: Message) -> None:
    if not _is_owner_admin(message):
        await message.answer("Команда доступна только владельцу.")
        return
    await message.answer(
        "🛠 <b>Административные команды</b>\n\n"
        "<code>/admin_beta TELEGRAM_ID 30</code>\n"
        "<code>/admin_beta TELEGRAM_ID unlimited</code>\n"
        "<code>/admin_user TELEGRAM_ID</code>\n"
        "<code>/admin_revoke_beta TELEGRAM_ID</code>"
    )


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
            BotCommand(command="start", description="Открыть Beauty Lead Finder"),
            BotCommand(command="menu", description="Главное меню"),
            BotCommand(command="plans", description="Тарифы"),
            BotCommand(command="limits", description="Мои лимиты"),
            BotCommand(command="myid", description="Показать Telegram ID"),
            BotCommand(command="support", description="Поддержка"),
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
    finally:
        db.close()

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
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
