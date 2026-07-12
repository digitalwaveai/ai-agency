from __future__ import annotations

import html
import os
from datetime import datetime
from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.database import SessionLocal
from app.services.lead_workspace_service import (
    CLOSED_STATUSES,
    PIPELINE_STATUSES,
    STATUS_LABELS,
    LeadWorkspaceError,
    PipelineLeadRow,
    export_pipeline_csv,
    get_pipeline_lead,
    list_due_follow_ups,
    list_pipeline_leads,
    pipeline_counts,
    save_lead_note,
    schedule_follow_up,
    status_label,
    update_pipeline_status,
)
from app.services.telegram_service import register_telegram_account
from app.telegram_projects import (
    BUTTON_EXPORT,
    BUTTON_PIPELINE,
    leadpilot_main_keyboard,
)


router = Router(name="leadpilot_lead_workspace")


class LeadWorkspaceFlow(StatesGroup):
    waiting_note = State()
    waiting_follow_up = State()


def _admin_telegram_id() -> str | None:
    value = os.getenv("ADMIN_TELEGRAM_ID")
    return value.strip() if value and value.strip() else None


def _ensure_user_id(message: Message) -> int:
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


def _ensure_callback_user_id(callback: CallbackQuery) -> int:
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


def _valid_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _source(row: PipelineLeadRow) -> str | None:
    lead = row.lead
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


def _contact(row: PipelineLeadRow) -> str:
    lead = row.lead
    values: list[str] = []
    if lead.phone:
        values.append(f"телефон: {lead.phone}")
    if lead.email:
        values.append(f"email: {lead.email}")
    if lead.whatsapp:
        values.append(f"WhatsApp: {lead.whatsapp}")
    return ", ".join(values) or "контакты в источнике"


def _format_datetime(value: datetime | None) -> str:
    return value.strftime("%d.%m.%Y %H:%M") if value else "не назначен"


def _format_card(row: PipelineLeadRow) -> str:
    workspace = row.workspace
    lines = [
        f"🎯 <b>{html.escape(row.lead.name)}</b>",
        f"Проект: <b>{html.escape(row.project.name)}</b>",
        f"Статус: <b>{html.escape(status_label(row.project_lead.status))}</b>",
        f"Рейтинг: <b>{int(row.lead.score or 0)}/100</b>",
        f"Контакт: {html.escape(_contact(row))}",
    ]

    if row.lead.pain_points:
        lines.extend(
            [
                "",
                "<b>Боль:</b>",
                html.escape(row.lead.pain_points[:650]),
            ]
        )
    if row.lead.suggested_offer:
        lines.extend(
            [
                "",
                "<b>Оффер:</b>",
                html.escape(row.lead.suggested_offer[:450]),
            ]
        )
    if workspace and workspace.note:
        lines.extend(
            [
                "",
                "<b>Заметка:</b>",
                html.escape(workspace.note[:700]),
            ]
        )
    if workspace and workspace.next_follow_up_at:
        lines.extend(
            [
                "",
                "<b>Следующий контакт:</b> "
                + html.escape(_format_datetime(workspace.next_follow_up_at)),
            ]
        )

    source = _source(row)
    if source:
        lines.extend(
            [
                "",
                f'<a href="{html.escape(source, quote=True)}">Открыть источник</a>',
            ]
        )

    return "\n".join(lines)


def _overview_keyboard(counts: dict[str, int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pairs = [
        ("found", "🆕"),
        ("saved", "📌"),
        ("contacted", "📨"),
        ("replied", "💬"),
        ("qualified", "⭐"),
        ("won", "🏆"),
        ("lost", "❌"),
    ]

    for index in range(0, len(pairs), 2):
        buttons = []
        for status, emoji in pairs[index:index + 2]:
            buttons.append(
                InlineKeyboardButton(
                    text=(
                        f"{emoji} {STATUS_LABELS[status]} "
                        f"({counts.get(status, 0)})"
                    ),
                    callback_data=f"pipe:list:{status}",
                )
            )
        rows.append(buttons)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="📋 Все лиды",
                    callback_data="pipe:list:all",
                ),
                InlineKeyboardButton(
                    text="⏰ Просроченные",
                    callback_data="pipe:due",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📤 Экспорт CSV",
                    callback_data="pipe:export",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _lead_actions_keyboard(
    project_lead_id: int,
    current_status: str,
) -> InlineKeyboardMarkup:
    status_buttons = [
        ("contacted", "📨 Связались"),
        ("replied", "💬 Ответил"),
        ("qualified", "⭐ Квалифицирован"),
        ("won", "🏆 Сделка"),
        ("lost", "❌ Отказ"),
    ]
    rows: list[list[InlineKeyboardButton]] = []

    for index in range(0, len(status_buttons), 2):
        current_row = []
        for status, label in status_buttons[index:index + 2]:
            marker = "✅ " if status == current_status else ""
            current_row.append(
                InlineKeyboardButton(
                    text=marker + label,
                    callback_data=f"pipe:set:{project_lead_id}:{status}",
                )
            )
        rows.append(current_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="📝 Заметка",
                    callback_data=f"pipe:note:{project_lead_id}",
                ),
                InlineKeyboardButton(
                    text="⏰ Контакт",
                    callback_data=f"pipe:follow:{project_lead_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К воронке",
                    callback_data="pipe:home",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_overview(message: Message, user_id: int) -> None:
    db = SessionLocal()
    try:
        counts = pipeline_counts(db, user_id=user_id)
    finally:
        db.close()

    total = sum(counts.values())
    await message.answer(
        "📈 <b>Воронка лидов</b>\n\n"
        f"Всего лидов: <b>{total}</b>\n"
        "Выберите этап, чтобы открыть список.",
        reply_markup=_overview_keyboard(counts),
    )


async def _send_rows(
    message: Message,
    *,
    user_id: int,
    status: str | None,
) -> None:
    db = SessionLocal()
    try:
        rows = list_pipeline_leads(
            db,
            user_id=user_id,
            status=status,
            limit=20,
        )
    finally:
        db.close()

    if not rows:
        label = status_label(status) if status else "Все лиды"
        await message.answer(
            f"В разделе «{html.escape(label)}» пока нет лидов.",
            reply_markup=leadpilot_main_keyboard(),
        )
        return

    title = status_label(status) if status else "Все лиды"
    await message.answer(
        f"📋 <b>{html.escape(title)}</b>\n"
        f"Показано: <b>{len(rows)}</b>"
    )

    for row in rows:
        await message.answer(
            _format_card(row),
            reply_markup=_lead_actions_keyboard(
                row.project_lead.id,
                row.project_lead.status,
            ),
            disable_web_page_preview=True,
        )


async def _send_export(message: Message, user_id: int) -> None:
    db = SessionLocal()
    try:
        rows = list_pipeline_leads(
            db,
            user_id=user_id,
            limit=500,
        )
        content = export_pipeline_csv(rows)
    finally:
        db.close()

    if not rows:
        await message.answer(
            "Экспортировать пока нечего. Сначала найдите клиентов."
        )
        return

    filename = f"leadpilot-leads-{datetime.now():%Y%m%d-%H%M}.csv"
    await message.answer_document(
        BufferedInputFile(content, filename=filename),
        caption=(
            f"📤 Экспортировано лидов: <b>{len(rows)}</b>\n"
            "Файл открывается в Excel и Google Таблицах."
        ),
    )


@router.message(Command("pipeline"))
@router.message(F.text == BUTTON_PIPELINE)
async def show_pipeline(message: Message) -> None:
    await _send_overview(message, _ensure_user_id(message))


@router.callback_query(F.data == "pipe:home")
async def pipeline_home(callback: CallbackQuery) -> None:
    user_id = _ensure_callback_user_id(callback)
    await callback.answer()
    if callback.message:
        await _send_overview(callback.message, user_id)


@router.callback_query(F.data.startswith("pipe:list:"))
async def pipeline_list(callback: CallbackQuery) -> None:
    user_id = _ensure_callback_user_id(callback)
    raw_status = (callback.data or "").split(":", 2)[2]
    status = None if raw_status == "all" else raw_status
    await callback.answer()
    if callback.message:
        await _send_rows(
            callback.message,
            user_id=user_id,
            status=status,
        )


@router.callback_query(F.data.startswith("pipe:set:"))
async def set_pipeline_status(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    project_lead_id = int(parts[2])
    status = parts[3]
    user_id = _ensure_callback_user_id(callback)

    db = SessionLocal()
    try:
        row = update_pipeline_status(
            db,
            user_id=user_id,
            project_lead_id=project_lead_id,
            status=status,
        )
    except LeadWorkspaceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    finally:
        db.close()

    await callback.answer(f"Статус: {status_label(status)}")
    if callback.message:
        await callback.message.edit_text(
            _format_card(row),
            reply_markup=_lead_actions_keyboard(
                row.project_lead.id,
                row.project_lead.status,
            ),
            disable_web_page_preview=True,
        )


@router.callback_query(F.data.startswith("pipe:note:"))
async def start_note(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    project_lead_id = int((callback.data or "").split(":")[2])
    user_id = _ensure_callback_user_id(callback)

    db = SessionLocal()
    try:
        get_pipeline_lead(
            db,
            user_id=user_id,
            project_lead_id=project_lead_id,
        )
    except LeadWorkspaceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    finally:
        db.close()

    await state.clear()
    await state.update_data(
        workspace_user_id=user_id,
        project_lead_id=project_lead_id,
    )
    await state.set_state(LeadWorkspaceFlow.waiting_note)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "📝 Напишите заметку по лиду.\n"
            "Например: «Отправил предложение, ждём ответ в пятницу»."
        )


@router.message(LeadWorkspaceFlow.waiting_note)
async def receive_note(
    message: Message,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    user_id = int(data["workspace_user_id"])
    project_lead_id = int(data["project_lead_id"])

    db = SessionLocal()
    try:
        row = save_lead_note(
            db,
            user_id=user_id,
            project_lead_id=project_lead_id,
            note=message.text or "",
        )
    except LeadWorkspaceError as exc:
        await message.answer(html.escape(str(exc)))
        return
    finally:
        db.close()

    await state.clear()
    await message.answer(
        "✅ Заметка сохранена.\n\n" + _format_card(row),
        reply_markup=_lead_actions_keyboard(
            row.project_lead.id,
            row.project_lead.status,
        ),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("pipe:follow:"))
async def start_follow_up(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    project_lead_id = int((callback.data or "").split(":")[2])
    user_id = _ensure_callback_user_id(callback)

    db = SessionLocal()
    try:
        get_pipeline_lead(
            db,
            user_id=user_id,
            project_lead_id=project_lead_id,
        )
    except LeadWorkspaceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    finally:
        db.close()

    await state.clear()
    await state.update_data(
        workspace_user_id=user_id,
        project_lead_id=project_lead_id,
    )
    await state.set_state(LeadWorkspaceFlow.waiting_follow_up)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "⏰ Укажите дату следующего контакта.\n\n"
            "Формат: <b>25.07.2026 14:30</b>\n"
            "Для удаления напоминания отправьте: <b>нет</b>"
        )


def _parse_follow_up(value: str) -> datetime | None:
    cleaned = " ".join((value or "").split()).strip().lower()
    if cleaned in {"нет", "удалить", "сбросить", "отмена"}:
        return None

    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            result = datetime.strptime(cleaned, fmt)
            if fmt == "%d.%m.%Y":
                result = result.replace(hour=10, minute=0)
            return result
        except ValueError:
            continue
    raise LeadWorkspaceError(
        "Неверный формат. Пример: 25.07.2026 14:30"
    )


@router.message(LeadWorkspaceFlow.waiting_follow_up)
async def receive_follow_up(
    message: Message,
    state: FSMContext,
) -> None:
    try:
        follow_up_at = _parse_follow_up(message.text or "")
    except LeadWorkspaceError as exc:
        await message.answer(html.escape(str(exc)))
        return

    if follow_up_at is not None and follow_up_at <= datetime.now():
        await message.answer("Дата должна быть в будущем.")
        return

    data = await state.get_data()
    user_id = int(data["workspace_user_id"])
    project_lead_id = int(data["project_lead_id"])

    db = SessionLocal()
    try:
        row = schedule_follow_up(
            db,
            user_id=user_id,
            project_lead_id=project_lead_id,
            follow_up_at=follow_up_at,
        )
    except LeadWorkspaceError as exc:
        await message.answer(html.escape(str(exc)))
        return
    finally:
        db.close()

    await state.clear()
    result_text = (
        f"✅ Следующий контакт: {_format_datetime(follow_up_at)}"
        if follow_up_at
        else "✅ Напоминание удалено"
    )
    await message.answer(
        result_text + "\n\n" + _format_card(row),
        reply_markup=_lead_actions_keyboard(
            row.project_lead.id,
            row.project_lead.status,
        ),
        disable_web_page_preview=True,
    )


@router.message(Command("followups"))
async def show_due_follow_ups(message: Message) -> None:
    user_id = _ensure_user_id(message)
    db = SessionLocal()
    try:
        rows = list_due_follow_ups(
            db,
            user_id=user_id,
            limit=20,
        )
    finally:
        db.close()

    if not rows:
        await message.answer("⏰ Просроченных контактов нет.")
        return

    await message.answer(
        f"⏰ <b>Просроченные контакты</b>\nПоказано: <b>{len(rows)}</b>"
    )
    for row in rows:
        await message.answer(
            _format_card(row),
            reply_markup=_lead_actions_keyboard(
                row.project_lead.id,
                row.project_lead.status,
            ),
            disable_web_page_preview=True,
        )


@router.callback_query(F.data == "pipe:due")
async def show_due_follow_ups_callback(callback: CallbackQuery) -> None:
    user_id = _ensure_callback_user_id(callback)
    db = SessionLocal()
    try:
        rows = list_due_follow_ups(
            db,
            user_id=user_id,
            limit=20,
        )
    finally:
        db.close()

    await callback.answer()
    if callback.message is None:
        return

    if not rows:
        await callback.message.answer("⏰ Просроченных контактов нет.")
        return

    for row in rows:
        await callback.message.answer(
            _format_card(row),
            reply_markup=_lead_actions_keyboard(
                row.project_lead.id,
                row.project_lead.status,
            ),
            disable_web_page_preview=True,
        )


@router.message(Command("export_leads"))
@router.message(F.text == BUTTON_EXPORT)
async def export_leads(message: Message) -> None:
    await _send_export(message, _ensure_user_id(message))


@router.callback_query(F.data == "pipe:export")
async def export_leads_callback(callback: CallbackQuery) -> None:
    user_id = _ensure_callback_user_id(callback)
    await callback.answer("Готовлю файл")
    if callback.message:
        await _send_export(callback.message, user_id)
