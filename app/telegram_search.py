from __future__ import annotations

import html
from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.database import SessionLocal
from app.models import Lead
from app.services.niche_profile_service import NicheProfileError
from app.services.project_search_service import (
    get_saved_search_location,
    list_active_projects,
    list_recent_user_leads,
    run_project_search,
)
from app.services.telegram_project_service import format_project_card
from app.services.telegram_service import register_telegram_account
from app.services.usage_service import UsageError, UsageLimitExceeded
from app.telegram_projects import (
    BUTTON_LEADS,
    BUTTON_SEARCH,
    leadpilot_main_keyboard,
)


router = Router(name="leadpilot_search")


class ProjectSearchFlow(StatesGroup):
    waiting_location = State()
    waiting_limit = State()


def _admin_telegram_id() -> str | None:
    import os

    value = os.getenv("ADMIN_TELEGRAM_ID")
    return value.strip() if value and value.strip() else None


def _ensure_user_id_from_message(message: Message) -> int:
    tg_user = message.from_user
    if tg_user is None:
        raise RuntimeError("Telegram-пользователь не определён")

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
        return account.user.id
    finally:
        db.close()


def _ensure_user_id_from_callback(callback: CallbackQuery) -> int:
    tg_user = callback.from_user
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
        return account.user.id
    finally:
        db.close()


def _project_keyboard(projects) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=project.name[:55],
                callback_data=f"sproject:{project.id}",
            )
        ]
        for project in projects
    ]
    rows.append(
        [InlineKeyboardButton(text="❌ Отмена", callback_data="search:cancel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _location_keyboard(saved: str | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if saved:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Использовать: {saved[:45]}",
                    callback_data="slocation:saved",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="❌ Отмена", callback_data="search:cancel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _limit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="3", callback_data="slimit:3"),
                InlineKeyboardButton(text="5", callback_data="slimit:5"),
                InlineKeyboardButton(text="10", callback_data="slimit:10"),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="search:cancel",
                )
            ],
        ]
    )


def _valid_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _lead_source(lead: Lead) -> str | None:
    for value in (
        lead.website_url,
        lead.telegram_url,
        lead.instagram_url,
        lead.vk_url,
        lead.youtube_url,
        lead.source_url,
    ):
        if _valid_url(value):
            return value
    return None


def _contact_text(lead: Lead) -> str:
    contacts: list[str] = []
    if lead.phone:
        contacts.append(f"телефон: {lead.phone}")
    if lead.email:
        contacts.append(f"email: {lead.email}")
    if lead.whatsapp:
        contacts.append(f"WhatsApp: {lead.whatsapp}")
    return ", ".join(contacts) or "контакты смотрите в источнике"


def format_lead_card(
    lead: Lead,
    *,
    project_name: str | None = None,
) -> str:
    lines = [f"🎯 <b>{html.escape(lead.name)}</b>"]
    if project_name:
        lines.append(f"Проект: <b>{html.escape(project_name)}</b>")
    lines.extend(
        [
            f"Рейтинг: <b>{int(lead.score or 0)}/100</b>",
            f"Контакт: {html.escape(_contact_text(lead))}",
        ]
    )
    if lead.pain_points:
        lines.extend(
            [
                "",
                "<b>Обнаруженная боль:</b>",
                html.escape(lead.pain_points[:700]),
            ]
        )
    if lead.suggested_offer:
        lines.extend(
            [
                "",
                "<b>Что предложить:</b>",
                html.escape(lead.suggested_offer[:500]),
            ]
        )
    source = _lead_source(lead)
    if source:
        lines.extend(
            [
                "",
                f'<a href="{html.escape(source, quote=True)}">Открыть источник</a>',
            ]
        )
    return "\n".join(lines)


async def _ask_location(
    message: Message,
    state: FSMContext,
    *,
    project_id: int,
    user_id: int,
) -> None:
    db = SessionLocal()
    try:
        saved = get_saved_search_location(
            db,
            project_id=project_id,
        )
    finally:
        db.close()

    await state.clear()
    await state.update_data(
        owner_user_id=user_id,
        project_id=project_id,
    )
    await state.set_state(ProjectSearchFlow.waiting_location)
    await message.answer(
        "🌍 <b>Где искать клиентов?</b>\n\n"
        "Напишите город, регион или страну.\n"
        "Примеры: <i>Москва</i>, <i>Казань, Россия</i>, "
        "<i>Весь интернет</i>.",
        reply_markup=_location_keyboard(saved),
    )


@router.message(Command("search_clients"))
@router.message(F.text == BUTTON_SEARCH)
async def start_project_search(
    message: Message,
    state: FSMContext,
) -> None:
    user_id = _ensure_user_id_from_message(message)
    db = SessionLocal()
    try:
        projects = list_active_projects(db, user_id=user_id)
    finally:
        db.close()

    if not projects:
        await message.answer(
            "Сначала создайте и завершите хотя бы один проект.\n\n"
            "Нажмите «➕ Новый проект», заполните анкету и активируйте его.",
            reply_markup=leadpilot_main_keyboard(),
        )
        return

    if len(projects) == 1:
        await _ask_location(
            message,
            state,
            project_id=projects[0].id,
            user_id=user_id,
        )
        return

    await state.clear()
    await state.update_data(owner_user_id=user_id)
    await message.answer(
        "🔎 <b>Найти клиентов</b>\n\n"
        "Выберите активный проект:",
        reply_markup=_project_keyboard(projects),
    )


@router.callback_query(F.data.startswith("psearch:"))
@router.callback_query(F.data.startswith("sproject:"))
async def choose_search_project(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    project_id = int((callback.data or "").split(":")[1])
    user_id = _ensure_user_id_from_callback(callback)
    await callback.answer()
    if callback.message:
        await _ask_location(
            callback.message,
            state,
            project_id=project_id,
            user_id=user_id,
        )


@router.callback_query(F.data == "search:cancel")
async def cancel_search(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.clear()
    await callback.answer("Поиск отменён")
    if callback.message:
        await callback.message.answer(
            "Поиск отменён.",
            reply_markup=leadpilot_main_keyboard(),
        )


@router.callback_query(
    ProjectSearchFlow.waiting_location,
    F.data == "slocation:saved",
)
async def use_saved_location(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    project_id = int(data["project_id"])
    db = SessionLocal()
    try:
        saved = get_saved_search_location(
            db,
            project_id=project_id,
        )
    finally:
        db.close()

    if not saved:
        await callback.answer("Сохранённый регион не найден", show_alert=True)
        return

    await state.update_data(location=saved)
    await state.set_state(ProjectSearchFlow.waiting_limit)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"Регион: <b>{html.escape(saved)}</b>\n\n"
            "Сколько лидов показать?",
            reply_markup=_limit_keyboard(),
        )


@router.message(ProjectSearchFlow.waiting_location)
async def receive_search_location(
    message: Message,
    state: FSMContext,
) -> None:
    location = " ".join((message.text or "").split()).strip()
    if len(location) < 2:
        await message.answer("Укажите город, регион или страну.")
        return
    if len(location) > 160:
        await message.answer("Регион слишком длинный. Максимум 160 символов.")
        return

    await state.update_data(location=location)
    await state.set_state(ProjectSearchFlow.waiting_limit)
    await message.answer(
        f"Регион: <b>{html.escape(location)}</b>\n\n"
        "Сколько лидов показать?",
        reply_markup=_limit_keyboard(),
    )


@router.callback_query(
    ProjectSearchFlow.waiting_limit,
    F.data.startswith("slimit:"),
)
async def execute_search(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    limit = int((callback.data or "").split(":")[1])
    data = await state.get_data()
    project_id = int(data["project_id"])
    user_id = int(data["owner_user_id"])
    location = str(data["location"])

    await callback.answer("Поиск запущен")
    if callback.message is None:
        return

    progress_message = await callback.message.answer(
        "🔍 Ищу подходящих клиентов и проверяю их страницы.\n"
        "Это может занять несколько минут."
    )

    db = SessionLocal()
    try:
        execution = await run_project_search(
            db,
            user_id=user_id,
            project_id=project_id,
            location=location,
            limit=limit,
            external_user_id=callback.from_user.id,
            username=callback.from_user.username,
            session_id=str(callback.message.chat.id),
        )
    except UsageLimitExceeded as exc:
        await progress_message.edit_text(
            "Лимит поисков исчерпан.\n\n"
            f"Использовано: <b>{exc.used}</b> из <b>{exc.limit}</b>."
        )
        await state.clear()
        return
    except UsageError as exc:
        await progress_message.edit_text(
            f"Поиск недоступен: {html.escape(str(exc))}"
        )
        await state.clear()
        return
    except (NicheProfileError, ValueError) as exc:
        await progress_message.edit_text(
            f"Не удалось подготовить проект: {html.escape(str(exc))}"
        )
        await state.clear()
        return
    except Exception:
        await progress_message.edit_text(
            "Не удалось выполнить поиск из-за технической ошибки. "
            "Лимит автоматически возвращён."
        )
        await state.clear()
        return
    finally:
        db.close()

    await state.clear()
    await progress_message.edit_text(
        "✅ <b>Поиск завершён</b>\n\n"
        f"Проект: <b>{html.escape(execution.project.name)}</b>\n"
        f"Найдено: <b>{len(execution.leads)}</b>\n"
        f"Время: <b>{max(1, execution.duration_ms // 1000)} сек.</b>"
    )

    if not execution.leads:
        await callback.message.answer(
            "Подходящие лиды не найдены. Попробуйте другой регион "
            "или уточните анкету проекта.",
            reply_markup=leadpilot_main_keyboard(),
        )
        return

    for lead in execution.leads:
        await callback.message.answer(
            format_lead_card(
                lead,
                project_name=execution.project.name,
            ),
            disable_web_page_preview=True,
        )

    await callback.message.answer(
        "Лиды добавлены в раздел «📋 Мои лиды».",
        reply_markup=leadpilot_main_keyboard(),
    )


@router.message(Command("my_leads"))
@router.message(F.text == BUTTON_LEADS)
async def show_real_user_leads(message: Message) -> None:
    user_id = _ensure_user_id_from_message(message)
    db = SessionLocal()
    try:
        rows = list_recent_user_leads(
            db,
            user_id=user_id,
            limit=10,
        )
    finally:
        db.close()

    if not rows:
        await message.answer(
            "📋 <b>Мои лиды</b>\n\n"
            "Пока пусто. Запустите поиск по активному проекту.",
            reply_markup=leadpilot_main_keyboard(),
        )
        return

    await message.answer(
        f"📋 <b>Последние лиды</b>\n\nПоказано: <b>{len(rows)}</b>"
    )
    for _, lead, project in rows:
        await message.answer(
            format_lead_card(lead, project_name=project.name),
            disable_web_page_preview=True,
        )
