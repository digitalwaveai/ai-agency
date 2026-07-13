from __future__ import annotations

import html
import os
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.database import SessionLocal
from app.services.pipeline_analytics_service import (
    PipelineAnalytics,
    build_pipeline_recommendations,
    calculate_pipeline_analytics,
    export_pipeline_analytics_csv,
)
from app.services.telegram_service import register_telegram_account
from app.telegram_projects import (
    BUTTON_LEAD_ANALYTICS,
    leadpilot_main_keyboard,
)


router = Router(name="leadpilot_pipeline_analytics")


def _admin_telegram_id() -> str | None:
    value = os.getenv("ADMIN_TELEGRAM_ID")
    return value.strip() if value and value.strip() else None


def _ensure_message_user_id(message: Message) -> int:
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


def _analytics_keyboard(
    analytics: PipelineAnalytics,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="🔄 Обновить",
                callback_data="analytics:refresh",
            ),
            InlineKeyboardButton(
                text="📤 CSV-отчёт",
                callback_data="analytics:export",
            ),
        ],
    ]

    for project in analytics.projects[:8]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📁 {project.project_name[:42]}",
                    callback_data=f"analytics:project:{project.project_id}",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_overview(
    analytics: PipelineAnalytics,
) -> str:
    counts = analytics.status_counts
    recommendations = build_pipeline_recommendations(analytics)

    lines = [
        "📊 <b>Аналитика лидов</b>",
        "",
        f"Всего лидов: <b>{analytics.total}</b>",
        f"Средний рейтинг: <b>{analytics.average_score}/100</b>",
        "",
        "<b>Воронка:</b>",
        f"🆕 Новые: <b>{counts.get('found', 0)}</b>",
        f"📌 Сохранены: <b>{counts.get('saved', 0)}</b>",
        f"📨 Связались: <b>{counts.get('contacted', 0)}</b>",
        f"💬 Ответили: <b>{counts.get('replied', 0)}</b>",
        f"⭐ Квалифицированы: <b>{counts.get('qualified', 0)}</b>",
        f"🏆 Сделки: <b>{counts.get('won', 0)}</b>",
        f"❌ Отказы: <b>{counts.get('lost', 0)}</b>",
        "",
        "<b>Конверсия:</b>",
        f"Контакт с найденными: <b>{analytics.contact_rate}%</b>",
        f"Ответ среди контактов: <b>{analytics.response_rate}%</b>",
        (
            "Квалификация среди ответивших: "
            f"<b>{analytics.qualification_rate}%</b>"
        ),
        (
            "Сделки среди закрытых: "
            f"<b>{analytics.closed_win_rate}%</b>"
        ),
        "",
        f"⏰ Просроченные контакты: <b>{analytics.overdue_followups}</b>",
        f"Активность за 7 дней: <b>{analytics.activities_7d}</b>",
        f"Активность за 30 дней: <b>{analytics.activities_30d}</b>",
        "",
        "<b>Рекомендации:</b>",
    ]

    for item in recommendations:
        lines.append("• " + html.escape(item))

    lines.extend(
        [
            "",
            (
                "Обновлено: "
                f"{analytics.generated_at.strftime('%d.%m.%Y %H:%M')}"
            ),
        ]
    )
    return "\n".join(lines)


def _find_project(
    analytics: PipelineAnalytics,
    project_id: int,
):
    for project in analytics.projects:
        if project.project_id == project_id:
            return project
    return None


def _format_project(
    analytics: PipelineAnalytics,
    project_id: int,
) -> str:
    project = _find_project(analytics, project_id)
    if project is None:
        return "Проект не найден или в нём пока нет лидов."

    counts = project.status_counts
    return "\n".join(
        [
            f"📁 <b>{html.escape(project.project_name)}</b>",
            "",
            f"Всего лидов: <b>{project.total}</b>",
            f"Средний рейтинг: <b>{project.average_score}/100</b>",
            "",
            f"🆕 Новые: <b>{counts.get('found', 0)}</b>",
            f"📌 Сохранены: <b>{counts.get('saved', 0)}</b>",
            f"📨 Связались: <b>{counts.get('contacted', 0)}</b>",
            f"💬 Ответили: <b>{counts.get('replied', 0)}</b>",
            f"⭐ Квалифицированы: <b>{counts.get('qualified', 0)}</b>",
            f"🏆 Сделки: <b>{counts.get('won', 0)}</b>",
            f"❌ Отказы: <b>{counts.get('lost', 0)}</b>",
            "",
            f"Контакт с лидами: <b>{project.contact_rate}%</b>",
            f"Ответ среди контактов: <b>{project.response_rate}%</b>",
            (
                "Квалификация ответивших: "
                f"<b>{project.qualification_rate}%</b>"
            ),
            (
                "Сделки среди закрытых: "
                f"<b>{project.closed_win_rate}%</b>"
            ),
        ]
    )


def _project_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Общая аналитика",
                    callback_data="analytics:refresh",
                )
            ]
        ]
    )


def _load_analytics(user_id: int) -> PipelineAnalytics:
    db = SessionLocal()
    try:
        return calculate_pipeline_analytics(
            db,
            user_id=user_id,
        )
    finally:
        db.close()


async def _send_analytics(
    message: Message,
    user_id: int,
) -> None:
    analytics = _load_analytics(user_id)
    await message.answer(
        _format_overview(analytics),
        reply_markup=_analytics_keyboard(analytics),
    )


async def _send_export(
    message: Message,
    user_id: int,
) -> None:
    analytics = _load_analytics(user_id)
    if analytics.total == 0:
        await message.answer(
            "Для отчёта пока нет данных. Сначала найдите клиентов."
        )
        return

    content = export_pipeline_analytics_csv(analytics)
    filename = (
        f"leadpilot-analytics-{datetime.now():%Y%m%d-%H%M}.csv"
    )
    await message.answer_document(
        BufferedInputFile(content, filename=filename),
        caption=(
            "📊 Отчёт по воронке готов.\n"
            "Файл открывается в Excel и Google Таблицах."
        ),
    )


@router.message(Command("lead_analytics"))
@router.message(F.text == BUTTON_LEAD_ANALYTICS)
async def show_lead_analytics(message: Message) -> None:
    await _send_analytics(
        message,
        _ensure_message_user_id(message),
    )


@router.callback_query(F.data == "analytics:refresh")
async def refresh_analytics(callback: CallbackQuery) -> None:
    user_id = _ensure_callback_user_id(callback)
    analytics = _load_analytics(user_id)
    await callback.answer("Обновлено")

    if callback.message:
        await callback.message.edit_text(
            _format_overview(analytics),
            reply_markup=_analytics_keyboard(analytics),
        )


@router.callback_query(F.data.startswith("analytics:project:"))
async def show_project_analytics(
    callback: CallbackQuery,
) -> None:
    project_id = int((callback.data or "").split(":")[2])
    user_id = _ensure_callback_user_id(callback)
    analytics = _load_analytics(user_id)

    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            _format_project(analytics, project_id),
            reply_markup=_project_keyboard(),
        )


@router.callback_query(F.data == "analytics:export")
async def export_analytics_callback(
    callback: CallbackQuery,
) -> None:
    user_id = _ensure_callback_user_id(callback)
    await callback.answer("Готовлю отчёт")
    if callback.message:
        await _send_export(callback.message, user_id)


@router.message(Command("export_analytics"))
async def export_analytics(message: Message) -> None:
    await _send_export(
        message,
        _ensure_message_user_id(message),
    )
