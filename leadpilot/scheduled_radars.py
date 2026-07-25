from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
)

from .bot import MENU, RADAR_LIMIT, RADAR_NICHES, USER_INPUT_FILTER
from .models import Lead
from .project_radars import _project_regions, _project_target

RADAR_INTERVAL = 18
RADAR_CUSTOM_INTERVAL = 19
SCHEDULER_POLL_SECONDS = 60
MIN_INTERVAL_HOURS = 1
MAX_INTERVAL_HOURS = 24 * 30


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _interval_label(hours: int) -> str:
    if hours % (24 * 7) == 0:
        weeks = hours // (24 * 7)
        return f"каждые {weeks} нед." if weeks > 1 else "раз в неделю"
    if hours % 24 == 0:
        days = hours // 24
        return f"каждые {days} дня" if days in {2, 3, 4} else f"каждые {days} дней"
    return f"каждые {hours} ч."


def _next_run_text(value: object) -> str:
    if value is None:
        return "не назначен"
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return "неизвестно"
    return parsed.strftime("%d.%m.%Y в %H:%M UTC")


def _parse_interval(value: str) -> int | None:
    text = " ".join(value.lower().replace("ё", "е").split())
    match = re.fullmatch(
        r"(\d+)\s*(ч|час|часа|часов|h|д|день|дня|дней|сутки|суток|"
        r"нед|неделя|недели|недель|w)?",
        text,
    )
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2) or "час"
    if unit.startswith(("д", "сут")):
        hours = amount * 24
    elif unit.startswith(("нед", "w")):
        hours = amount * 24 * 7
    else:
        hours = amount
    return hours if MIN_INTERVAL_HOURS <= hours <= MAX_INTERVAL_HOURS else None


def _projects_keyboard(projects: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"📁 {project['name']}",
                callback_data=f"scheduled_radar_project:{project['id']}",
            )
        ]
        for project in projects
    ]
    rows.append(
        [InlineKeyboardButton("➕ Новый проект", callback_data="scheduled_radar_project:new")]
    )
    rows.append(
        [InlineKeyboardButton("❌ Отмена", callback_data="scheduled_radar_project:cancel")]
    )
    return InlineKeyboardMarkup(rows)


def _interval_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Каждые 24 часа", callback_data="radar_interval:24"),
            ],
            [
                InlineKeyboardButton("Каждые 3 дня", callback_data="radar_interval:72"),
            ],
            [
                InlineKeyboardButton("Раз в неделю", callback_data="radar_interval:168"),
            ],
            [
                InlineKeyboardButton("✍️ Свой период", callback_data="radar_interval:custom"),
            ],
            [InlineKeyboardButton("❌ Отмена", callback_data="radar_interval:cancel")],
        ]
    )


def _ensure_schema(database: Any) -> None:
    columns = (
        ("chat_id", "BIGINT"),
        ("interval_hours", "INTEGER NOT NULL DEFAULT 0"),
        ("next_run_at", "TIMESTAMP"),
        ("last_run_at", "TIMESTAMP"),
        ("last_error", "TEXT NOT NULL DEFAULT ''"),
    )
    with database._connect() as connection:
        if database.is_postgres:
            for name, definition in columns:
                connection.execute(
                    f"ALTER TABLE radars ADD COLUMN IF NOT EXISTS {name} {definition}"
                )
        else:
            existing = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(radars)").fetchall()
            }
            for name, definition in columns:
                if name not in existing:
                    connection.execute(
                        f"ALTER TABLE radars ADD COLUMN {name} {definition}"
                    )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_radars_due "
            "ON radars (active, next_run_at)"
        )
        connection.commit()


def _create_scheduled_radar(
    database: Any,
    *,
    user_id: int,
    chat_id: int,
    project_id: int,
    target: str,
    regions: list[str],
    result_limit: int,
    interval_hours: int,
) -> tuple[int, datetime]:
    next_run_at = _now() + timedelta(hours=interval_hours)
    statement = database._sql(
        """
        INSERT INTO radars (
            user_id, niches, regions, result_limit, active, project_id,
            chat_id, interval_hours, next_run_at, last_error
        )
        VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, '')
        RETURNING id
        """
    )
    with database._connect() as connection:
        row = connection.execute(
            statement,
            (
                user_id,
                target,
                "\n".join(regions),
                result_limit,
                project_id,
                chat_id,
                interval_hours,
                database._db_datetime(next_run_at),
            ),
        ).fetchone()
        connection.commit()
    return int(row["id"]), next_run_at


def _list_scheduled_radars(
    database: Any, user_id: int, limit: int = 20
) -> list[dict[str, Any]]:
    statement = database._sql(
        """
        SELECT r.id, r.user_id, r.chat_id, r.project_id, r.result_limit,
               r.interval_hours, r.next_run_at, r.last_run_at, r.last_error,
               r.active, r.niches, r.regions, p.name AS project_name
        FROM radars r
        LEFT JOIN projects p
          ON p.id = r.project_id AND p.user_id = r.user_id
        WHERE r.user_id = ? AND r.interval_hours > 0
        ORDER BY r.id DESC
        LIMIT ?
        """
    )
    with database._connect() as connection:
        rows = connection.execute(statement, (user_id, limit)).fetchall()
    return [dict(row) for row in rows]


def _get_scheduled_radar(
    database: Any, user_id: int, radar_id: int
) -> dict[str, Any] | None:
    statement = database._sql(
        """
        SELECT r.id, r.user_id, r.chat_id, r.project_id, r.result_limit,
               r.interval_hours, r.next_run_at, r.last_run_at, r.last_error,
               r.active, r.niches, r.regions, p.name AS project_name
        FROM radars r
        LEFT JOIN projects p
          ON p.id = r.project_id AND p.user_id = r.user_id
        WHERE r.user_id = ? AND r.id = ? AND r.interval_hours > 0
        """
    )
    with database._connect() as connection:
        row = connection.execute(statement, (user_id, radar_id)).fetchone()
    return dict(row) if row else None


def _set_radar_active(
    database: Any, user_id: int, radar_id: int, active: bool
) -> dict[str, Any] | None:
    radar = _get_scheduled_radar(database, user_id, radar_id)
    if not radar:
        return None
    next_run_at = (
        _now() + timedelta(hours=int(radar["interval_hours"])) if active else None
    )
    statement = database._sql(
        """
        UPDATE radars
        SET active = ?, next_run_at = ?, last_error = ''
        WHERE user_id = ? AND id = ? AND interval_hours > 0
        """
    )
    with database._connect() as connection:
        connection.execute(
            statement,
            (
                1 if active else 0,
                database._db_datetime(next_run_at) if next_run_at else None,
                user_id,
                radar_id,
            ),
        )
        connection.commit()
    radar["active"] = 1 if active else 0
    radar["next_run_at"] = next_run_at
    return radar


def _mark_radar_error(database: Any, radar_id: int, error: str) -> None:
    statement = database._sql(
        "UPDATE radars SET last_error = ? WHERE id = ?"
    )
    with database._connect() as connection:
        connection.execute(statement, (error[:1000], radar_id))
        connection.commit()


def _deactivate_radar(database: Any, radar_id: int, error: str = "") -> None:
    statement = database._sql(
        "UPDATE radars SET active = 0, next_run_at = NULL, last_error = ? WHERE id = ?"
    )
    with database._connect() as connection:
        connection.execute(statement, (error[:1000], radar_id))
        connection.commit()


def _claim_due_radars(database: Any, limit: int = 10) -> list[dict[str, Any]]:
    now = _now()
    select_sql = """
        SELECT id, user_id, chat_id, project_id, result_limit,
               interval_hours, next_run_at, niches, regions
        FROM radars
        WHERE active = 1
          AND interval_hours > 0
          AND next_run_at IS NOT NULL
          AND next_run_at <= ?
        ORDER BY next_run_at ASC
        LIMIT ?
    """
    if database.is_postgres:
        select_sql += " FOR UPDATE SKIP LOCKED"
    statement = database._sql(select_sql)
    update = database._sql(
        """
        UPDATE radars
        SET last_run_at = ?, next_run_at = ?, last_error = ''
        WHERE id = ?
        """
    )
    claimed: list[dict[str, Any]] = []
    with database._connect() as connection:
        rows = connection.execute(
            statement,
            (database._db_datetime(now), limit),
        ).fetchall()
        for raw in rows:
            radar = dict(raw)
            next_run_at = now + timedelta(hours=int(radar["interval_hours"]))
            connection.execute(
                update,
                (
                    database._db_datetime(now),
                    database._db_datetime(next_run_at),
                    int(radar["id"]),
                ),
            )
            radar["next_run_at"] = next_run_at
            claimed.append(radar)
        connection.commit()
    return claimed


def _existing_sources(database: Any, user_id: int, leads: list[Lead]) -> set[str]:
    sources = list(dict.fromkeys(lead.source_url for lead in leads if lead.source_url))
    if not sources:
        return set()
    statement = database._sql(
        "SELECT source_url FROM leads WHERE user_id = ? AND source_url = ?"
    )
    found: set[str] = set()
    with database._connect() as connection:
        for source in sources:
            row = connection.execute(statement, (user_id, source)).fetchone()
            if row:
                found.add(str(row["source_url"]))
    return found


async def _send_status(application: Any, chat_id: int, text: str) -> None:
    try:
        await application.bot.send_message(chat_id=chat_id, text=text, reply_markup=MENU)
    except Exception:
        logging.exception("Failed to send scheduled radar status")


async def _execute_scheduled_radar(
    owner: Any,
    application: Any,
    radar: dict[str, Any],
    *,
    manual: bool = False,
) -> int:
    radar_id = int(radar["id"])
    user_id = int(radar["user_id"])
    chat_id = int(radar.get("chat_id") or user_id)
    project_id = radar.get("project_id")
    if project_id is None:
        _deactivate_radar(owner.db, radar_id, "Проект не привязан")
        await _send_status(
            application,
            chat_id,
            f"⏸ Радар ID {radar_id} остановлен: к нему не привязан проект.",
        )
        return 0

    project = await asyncio.to_thread(
        owner.db.get_project, user_id, int(project_id)
    )
    if not project:
        _deactivate_radar(owner.db, radar_id, "Проект удалён или недоступен")
        await _send_status(
            application,
            chat_id,
            f"⏸ Радар ID {radar_id} остановлен: проект больше недоступен.",
        )
        return 0

    snapshot = await asyncio.to_thread(owner.db.get_usage_snapshot, user_id)
    if not snapshot.get("active"):
        _deactivate_radar(owner.db, radar_id, "Нет активного доступа")
        await _send_status(
            application,
            chat_id,
            f"⏸ Радар ID {radar_id} остановлен: пробный или оплаченный доступ закончился.",
        )
        return 0

    if not snapshot.get("unlimited"):
        if snapshot.get("remaining", {}).get("searches", 0) <= 0:
            _deactivate_radar(owner.db, radar_id, "Закончился лимит поисков")
            await _send_status(
                application,
                chat_id,
                f"⏸ Радар ID {radar_id} остановлен: закончился лимит поисков.",
            )
            return 0
        if snapshot.get("remaining", {}).get("leads", 0) <= 0:
            _deactivate_radar(owner.db, radar_id, "Закончился лимит лидов")
            await _send_status(
                application,
                chat_id,
                f"⏸ Радар ID {radar_id} остановлен: закончился лимит лидов.",
            )
            return 0

    ok, _ = await asyncio.to_thread(
        owner.db.consume_usage, user_id, "searches", 1
    )
    if not ok:
        _deactivate_radar(owner.db, radar_id, "Не удалось списать поиск")
        return 0

    target = _project_target(project)
    regions = _project_regions(project)
    result_limit = max(1, min(int(radar.get("result_limit") or 5), 10))
    candidate_limit = min(max(result_limit * 3, 10), 20)
    candidates: dict[str, Lead] = {}

    try:
        for region in regions[:3]:
            leads = await asyncio.to_thread(
                owner.search_client.search,
                target,
                region,
                candidate_limit,
            )
            for lead in leads:
                if lead.source_url and lead.source_url not in candidates:
                    candidates[lead.source_url] = lead
    except Exception as exc:
        logging.exception("Scheduled radar search failed")
        await asyncio.to_thread(owner.db.refund_usage, user_id, "searches", 1)
        _mark_radar_error(owner.db, radar_id, str(exc))
        if manual:
            await _send_status(
                application,
                chat_id,
                f"Не удалось запустить радар ID {radar_id}. Попробуйте позже.",
            )
        return 0

    leads = list(candidates.values())
    existing = await asyncio.to_thread(_existing_sources, owner.db, user_id, leads)
    new_leads = [lead for lead in leads if lead.source_url not in existing][
        :result_limit
    ]
    if not new_leads:
        await asyncio.to_thread(owner.db.refund_usage, user_id, "searches", 1)
        _mark_radar_error(owner.db, radar_id, "")
        if manual:
            await _send_status(
                application,
                chat_id,
                f"📡 Радар ID {radar_id}: новых подходящих лидов пока нет.",
            )
        return 0

    ids = await asyncio.to_thread(
        owner.db.save_leads,
        user_id,
        new_leads,
        int(project_id),
    )
    for lead, lead_id in zip(new_leads, ids, strict=True):
        lead.id = lead_id
    if not new_leads:
        return 0

    _mark_radar_error(owner.db, radar_id, "")
    title = str(project.get("name") or "Проект")
    text = (
        f"📡 Радар ID {radar_id} нашёл новые лиды\n"
        f"Проект: {title}\n"
        f"Новых лидов: {len(new_leads)}\n\n"
        + owner.format_leads(new_leads)
    )
    try:
        await application.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=MENU,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logging.exception("Failed to deliver scheduled radar leads")
        _mark_radar_error(owner.db, radar_id, f"Ошибка отправки: {exc}")
    return len(new_leads)


async def _scheduler_loop(owner: Any, application: Any) -> None:
    while True:
        try:
            due = await asyncio.to_thread(_claim_due_radars, owner.db, 10)
            for radar in due:
                try:
                    await _execute_scheduled_radar(owner, application, radar)
                except Exception as exc:
                    logging.exception("Unexpected scheduled radar failure")
                    _mark_radar_error(owner.db, int(radar["id"]), str(exc))
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Scheduled radar loop failed")
        await asyncio.sleep(SCHEDULER_POLL_SECONDS)


def install_scheduled_radars(bot_class: type[Any], database_class: type[Any]) -> None:
    """Turn project radars into persistent automatic Telegram monitoring."""
    if getattr(bot_class, "_scheduled_radars_installed", False):
        return

    old_init_schema = database_class.init_schema

    @wraps(old_init_schema)
    def init_schema(self: Any) -> None:
        old_init_schema(self)
        _ensure_schema(self)

    database_class.init_schema = init_schema
    database_class.create_scheduled_radar = lambda self, **kwargs: _create_scheduled_radar(
        self, **kwargs
    )
    database_class.list_scheduled_radars = lambda self, user_id, limit=20: _list_scheduled_radars(
        self, user_id, limit
    )
    database_class.get_scheduled_radar = lambda self, user_id, radar_id: _get_scheduled_radar(
        self, user_id, radar_id
    )
    database_class.set_scheduled_radar_active = (
        lambda self, user_id, radar_id, active: _set_radar_active(
            self, user_id, radar_id, active
        )
    )

    async def _resource_available(
        self: Any, update: Update, field: str
    ) -> bool:
        self.ensure_account(update)
        user = update.effective_user
        message = update.effective_message
        if not user or not message:
            return False
        snapshot = await asyncio.to_thread(self.db.get_usage_snapshot, user.id)
        if snapshot.get("unlimited"):
            return True
        if not snapshot.get("active"):
            await self.reject(update)
            return False
        if snapshot.get("remaining", {}).get(field, 0) <= 0:
            labels = {
                "radars": "радаров",
                "searches": "поисков",
                "leads": "лидов",
            }
            await message.reply_text(
                f"⛔ Лимит {labels[field]} исчерпан.\n\n"
                "Откройте «⭐ Тарифы», чтобы увеличить лимиты.",
                reply_markup=MENU,
            )
            return False
        return True

    async def radar_start(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        if not self.authorized(update):
            return await self.reject(update)
        if not await _resource_available(self, update, "radars"):
            return ConversationHandler.END
        context.user_data.clear()
        user_id = update.effective_user.id
        projects, radars = await asyncio.gather(
            asyncio.to_thread(self.db.list_projects, user_id, 20),
            asyncio.to_thread(self.db.list_scheduled_radars, user_id, 10),
        )
        lines = [
            "📡 Автоматические радары",
            "",
            "Выберите проект, по которому бот будет автоматически искать "
            "новых лидов и присылать их в этот чат.",
        ]
        if radars:
            lines.extend(["", "Ваши радары:"])
            for radar in radars:
                status = "активен" if int(radar.get("active") or 0) else "остановлен"
                next_text = (
                    _next_run_text(radar.get("next_run_at"))
                    if status == "активен"
                    else "—"
                )
                lines.append(
                    f"ID {radar['id']} · {radar.get('project_name') or 'Проект'} · "
                    f"{_interval_label(int(radar['interval_hours']))} · {status}\n"
                    f"Следующий запуск: {next_text}"
                )
            lines.extend(
                [
                    "",
                    "Остановить: /radar_stop ID",
                    "Возобновить: /radar_resume ID",
                    "Проверить сейчас: /radar_run ID",
                ]
            )
        if not projects:
            lines.extend(
                ["", "Проектов пока нет. Нажмите «➕ Новый проект»." ]
            )
        await update.effective_message.reply_text(
            "\n".join(lines),
            reply_markup=_projects_keyboard(projects),
        )
        return RADAR_NICHES

    async def select_project(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        query = update.callback_query
        if not query or not query.from_user:
            return ConversationHandler.END
        await query.answer()
        value = (query.data or "").partition(":")[2]
        if value == "cancel":
            context.user_data.clear()
            await query.message.reply_text("Настройка радара отменена.", reply_markup=MENU)
            return ConversationHandler.END
        if value == "new":
            context.user_data.clear()
            return await self.new_project_start(update, context)
        try:
            project_id = int(value)
        except ValueError:
            await query.message.reply_text(
                "Не удалось выбрать проект. Откройте «📡 Радары» ещё раз.",
                reply_markup=MENU,
            )
            return ConversationHandler.END
        project = await asyncio.to_thread(
            self.db.get_project, query.from_user.id, project_id
        )
        if not project:
            await query.message.reply_text("Проект не найден.", reply_markup=MENU)
            return ConversationHandler.END
        context.user_data["scheduled_radar_project"] = project
        await query.message.reply_text(
            f"📡 Проект: {project['name']}\n\n"
            "Как часто автоматически искать новых лидов?",
            reply_markup=_interval_keyboard(),
        )
        return RADAR_INTERVAL

    async def select_interval(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        query = update.callback_query
        if not query:
            return ConversationHandler.END
        await query.answer()
        value = (query.data or "").partition(":")[2]
        if value == "cancel":
            context.user_data.clear()
            await query.message.reply_text("Настройка радара отменена.", reply_markup=MENU)
            return ConversationHandler.END
        if value == "custom":
            await query.message.reply_text(
                "Введите свой период от 1 часа до 30 дней.\n"
                "Примеры: 12 часов, 2 дня, 3 недели."
            )
            return RADAR_CUSTOM_INTERVAL
        try:
            hours = int(value)
        except ValueError:
            return RADAR_INTERVAL
        context.user_data["scheduled_radar_interval_hours"] = hours
        await query.message.reply_text(
            f"Период: {_interval_label(hours)}.\n\n"
            "Сколько новых лидов присылать за один запуск? Введите число от 1 до 5."
        )
        return RADAR_LIMIT

    async def receive_custom_interval(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        hours = _parse_interval(update.effective_message.text or "")
        if hours is None:
            await update.effective_message.reply_text(
                "Не удалось распознать период. Введите от 1 часа до 30 дней.\n"
                "Например: 12 часов, 2 дня или 1 неделя."
            )
            return RADAR_CUSTOM_INTERVAL
        context.user_data["scheduled_radar_interval_hours"] = hours
        await update.effective_message.reply_text(
            f"Период: {_interval_label(hours)}.\n\n"
            "Сколько новых лидов присылать за один запуск? Введите число от 1 до 5."
        )
        return RADAR_LIMIT

    async def receive_limit(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        try:
            result_limit = int((update.effective_message.text or "").strip())
            if not 1 <= result_limit <= 5:
                raise ValueError
        except ValueError:
            await update.effective_message.reply_text("Введите число от 1 до 5.")
            return RADAR_LIMIT
        project = dict(context.user_data.get("scheduled_radar_project") or {})
        interval_hours = int(
            context.user_data.get("scheduled_radar_interval_hours") or 0
        )
        if not project or not interval_hours:
            context.user_data.clear()
            await update.effective_message.reply_text(
                "Настройки радара потеряны. Откройте «📡 Радары» ещё раз.",
                reply_markup=MENU,
            )
            return ConversationHandler.END
        if not await _resource_available(self, update, "radars"):
            context.user_data.clear()
            return ConversationHandler.END
        user_id = update.effective_user.id
        ok, snapshot = await asyncio.to_thread(
            self.db.consume_usage, user_id, "radars", 1
        )
        if not ok:
            await update.effective_message.reply_text(
                "⛔ Не удалось создать радар: лимит радаров исчерпан.",
                reply_markup=MENU,
            )
            context.user_data.clear()
            return ConversationHandler.END
        try:
            radar_id, next_run_at = await asyncio.to_thread(
                self.db.create_scheduled_radar,
                user_id=user_id,
                chat_id=update.effective_chat.id,
                project_id=int(project["id"]),
                target=_project_target(project),
                regions=_project_regions(project),
                result_limit=result_limit,
                interval_hours=interval_hours,
            )
        except Exception:
            logging.exception("Scheduled radar creation failed")
            if not snapshot.get("unlimited"):
                await asyncio.to_thread(self.db.refund_usage, user_id, "radars", 1)
            await update.effective_message.reply_text(
                "Не удалось сохранить радар. Попробуйте ещё раз.", reply_markup=MENU
            )
            context.user_data.clear()
            return ConversationHandler.END
        context.user_data.clear()
        await update.effective_message.reply_text(
            "✅ Автоматический радар создан\n\n"
            f"ID: {radar_id}\n"
            f"Проект: {project['name']}\n"
            f"Период: {_interval_label(interval_hours)}\n"
            f"Лидов за запуск: до {result_limit}\n"
            f"Первый запуск: {_next_run_text(next_run_at)}\n\n"
            "Бот будет присылать только новые релевантные лиды. Уже найденные "
            "компании повторно не отправляются.\n\n"
            f"Остановить радар: /radar_stop {radar_id}",
            reply_markup=MENU,
        )
        return ConversationHandler.END

    @staticmethod
    def _command_id(context: ContextTypes.DEFAULT_TYPE) -> int | None:
        if len(context.args) != 1:
            return None
        try:
            value = int(context.args[0])
        except ValueError:
            return None
        return value if value > 0 else None

    async def radar_stop(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        radar_id = _command_id(context)
        if radar_id is None:
            await update.effective_message.reply_text(
                "Формат: /radar_stop ID", reply_markup=MENU
            )
            return
        radar = await asyncio.to_thread(
            self.db.set_scheduled_radar_active,
            update.effective_user.id,
            radar_id,
            False,
        )
        await update.effective_message.reply_text(
            (
                f"⏸ Радар ID {radar_id} остановлен."
                if radar
                else "Радар с таким ID не найден."
            ),
            reply_markup=MENU,
        )

    async def radar_resume(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        radar_id = _command_id(context)
        if radar_id is None:
            await update.effective_message.reply_text(
                "Формат: /radar_resume ID", reply_markup=MENU
            )
            return
        if not await _resource_available(self, update, "searches"):
            return
        radar = await asyncio.to_thread(
            self.db.set_scheduled_radar_active,
            update.effective_user.id,
            radar_id,
            True,
        )
        await update.effective_message.reply_text(
            (
                f"▶️ Радар ID {radar_id} снова активен.\n"
                f"Следующий запуск: {_next_run_text(radar['next_run_at'])}"
                if radar
                else "Радар с таким ID не найден."
            ),
            reply_markup=MENU,
        )

    async def radar_run(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        radar_id = _command_id(context)
        if radar_id is None:
            await update.effective_message.reply_text(
                "Формат: /radar_run ID", reply_markup=MENU
            )
            return ConversationHandler.END
        radar = await asyncio.to_thread(
            self.db.get_scheduled_radar, update.effective_user.id, radar_id
        )
        if not radar:
            await update.effective_message.reply_text(
                "Автоматический радар с таким ID не найден.", reply_markup=MENU
            )
            return ConversationHandler.END
        radar["chat_id"] = update.effective_chat.id
        await update.effective_message.reply_text(
            f"📡 Проверяю радар ID {radar_id} прямо сейчас…"
        )
        await _execute_scheduled_radar(
            self, context.application, radar, manual=True
        )
        return ConversationHandler.END

    bot_class.radar_start = radar_start
    bot_class.select_scheduled_radar_project = select_project
    bot_class.select_scheduled_radar_interval = select_interval
    bot_class.receive_scheduled_radar_custom_interval = receive_custom_interval
    bot_class.receive_radar_limit = receive_limit
    bot_class.radar_stop = radar_stop
    bot_class.radar_resume = radar_resume
    bot_class.radar_run = radar_run

    old_build_application = bot_class.build_application

    @wraps(old_build_application)
    def build_application(self: Any):
        application = old_build_application(self)
        conversation = None
        for handlers in application.handlers.values():
            for handler in handlers:
                if isinstance(handler, ConversationHandler) and RADAR_NICHES in handler.states:
                    conversation = handler
                    break
            if conversation is not None:
                break
        if conversation is None:
            raise RuntimeError("Не найден основной ConversationHandler LeadPilot")
        conversation.states[RADAR_NICHES] = [
            CallbackQueryHandler(
                self.select_scheduled_radar_project,
                pattern=r"^scheduled_radar_project:(?:\d+|new|cancel)$",
            )
        ]
        conversation.states[RADAR_INTERVAL] = [
            CallbackQueryHandler(
                self.select_scheduled_radar_interval,
                pattern=r"^radar_interval:(?:24|72|168|custom|cancel)$",
            )
        ]
        conversation.states[RADAR_CUSTOM_INTERVAL] = [
            MessageHandler(
                USER_INPUT_FILTER,
                self.receive_scheduled_radar_custom_interval,
            )
        ]
        conversation.states[RADAR_LIMIT] = [
            MessageHandler(USER_INPUT_FILTER, self.receive_radar_limit)
        ]
        application.add_handler(CommandHandler("radar_stop", self.radar_stop))
        application.add_handler(CommandHandler("radar_resume", self.radar_resume))

        previous_post_init = application.post_init
        previous_post_shutdown = application.post_shutdown

        async def post_init(app: Any) -> None:
            if previous_post_init is not None:
                await previous_post_init(app)
            task = asyncio.create_task(
                _scheduler_loop(self, app),
                name="leadpilot-scheduled-radars",
            )
            app.bot_data["scheduled_radars_task"] = task
            logging.info("Scheduled radar worker started")

        async def post_shutdown(app: Any) -> None:
            task = app.bot_data.pop("scheduled_radars_task", None)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if previous_post_shutdown is not None:
                await previous_post_shutdown(app)

        application.post_init = post_init
        application.post_shutdown = post_shutdown
        return application

    bot_class.build_application = build_application
    bot_class._scheduled_radars_installed = True
