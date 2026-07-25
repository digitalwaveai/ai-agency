from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
)

from .bot import (
    MENU,
    RADAR_LIMIT,
    RADAR_NICHES,
    USER_INPUT_FILTER,
)
from .models import Lead
from .project_search_context import EXCLUSION_SEPARATOR, TARGET_SEPARATOR


def _radar_projects_keyboard(
    projects: list[dict[str, Any]],
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"📁 {project['name']}",
                callback_data=f"radar_project:{project['id']}",
            )
        ]
        for project in projects
    ]
    rows.append(
        [
            InlineKeyboardButton(
                "➕ Новый проект",
                callback_data="radar_project:new",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                "❌ Отмена",
                callback_data="radar_project:cancel",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def _project_target(project: dict[str, Any]) -> str:
    audience = str(
        project.get("target_audience") or project.get("niche") or ""
    ).strip()
    priorities = str(project.get("priorities") or "").strip()
    niche = str(project.get("niche") or "").strip()
    exclusions = str(project.get("exclusions") or "").strip()

    primary_parts = [audience]
    if priorities and priorities.lower() not in audience.lower():
        primary_parts.append(priorities)
    primary = " ".join(part for part in primary_parts if part).strip()

    target = primary or niche
    if (
        niche
        and niche.lower() not in target.lower()
        and target.lower() not in niche.lower()
    ):
        target = f"{target}{TARGET_SEPARATOR}{niche}"
    if exclusions:
        target = f"{target}{EXCLUSION_SEPARATOR}{exclusions}"
    return target


def _project_regions(project: dict[str, Any]) -> list[str]:
    raw = str(project.get("region") or "").strip()
    if not raw:
        return ["Россия"]
    values = [
        item.strip()
        for item in raw.replace(";", ",").split(",")
        if item.strip()
    ]
    return list(dict.fromkeys(values))[:3] or [raw]


def _ensure_radar_project_schema(database: Any) -> None:
    with database._connect() as connection:
        if database.is_postgres:
            connection.execute(
                "ALTER TABLE radars ADD COLUMN IF NOT EXISTS project_id BIGINT"
            )
        else:
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(radars)").fetchall()
            }
            if "project_id" not in columns:
                connection.execute("ALTER TABLE radars ADD COLUMN project_id BIGINT")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_radars_user_project "
            "ON radars (user_id, project_id)"
        )
        connection.commit()


def install_project_radars(
    bot_class: type[Any], database_class: type[Any]
) -> None:
    """Make radars project-based and reuse strict project search quality."""
    if getattr(bot_class, "_project_radars_installed", False):
        return

    old_init_schema = database_class.init_schema

    @wraps(old_init_schema)
    def init_schema(self: Any) -> None:
        old_init_schema(self)
        _ensure_radar_project_schema(self)

    def create_radar(
        self: Any,
        user_id: int,
        niches: list[str],
        regions: list[str],
        result_limit: int,
        project_id: int | None = None,
    ) -> int:
        statement = self._sql(
            """
            INSERT INTO radars (
                user_id, niches, regions, result_limit, project_id
            )
            VALUES (?, ?, ?, ?, ?)
            RETURNING id
            """
        )
        with self._connect() as connection:
            row = connection.execute(
                statement,
                (
                    user_id,
                    "\n".join(niches),
                    "\n".join(regions),
                    result_limit,
                    project_id,
                ),
            ).fetchone()
            connection.commit()
        return int(row["id"])

    def list_radars(
        self: Any, user_id: int, limit: int = 10
    ) -> list[dict[str, Any]]:
        statement = self._sql(
            """
            SELECT r.id, r.niches, r.regions, r.result_limit, r.active,
                   r.project_id, r.created_at, p.name AS project_name
            FROM radars r
            LEFT JOIN projects p
              ON p.id = r.project_id AND p.user_id = r.user_id
            WHERE r.user_id = ?
            ORDER BY r.id DESC
            LIMIT ?
            """
        )
        with self._connect() as connection:
            rows = connection.execute(statement, (user_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def get_radar(
        self: Any, user_id: int, radar_id: int
    ) -> dict[str, Any] | None:
        statement = self._sql(
            """
            SELECT r.id, r.niches, r.regions, r.result_limit, r.active,
                   r.project_id, r.created_at, p.name AS project_name
            FROM radars r
            LEFT JOIN projects p
              ON p.id = r.project_id AND p.user_id = r.user_id
            WHERE r.user_id = ? AND r.id = ?
            """
        )
        with self._connect() as connection:
            row = connection.execute(statement, (user_id, radar_id)).fetchone()
        return dict(row) if row else None

    async def radar_start(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        if not self.authorized(update):
            return await self.reject(update)
        context.user_data.clear()
        projects = await asyncio.to_thread(
            self.db.list_projects, update.effective_user.id, 20
        )
        text = (
            "📡 Радары\n\n"
            "Выберите проект. Радар возьмёт из его анкеты нишу, целевого "
            "клиента, регион, приоритеты и исключения."
        )
        if not projects:
            text += (
                "\n\nУ вас пока нет проектов. Нажмите «➕ Новый проект», "
                "чтобы создать первый."
            )
        await update.effective_message.reply_text(
            text,
            reply_markup=_radar_projects_keyboard(projects),
        )
        return RADAR_NICHES

    async def select_radar_project(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        query = update.callback_query
        if not query or not query.from_user:
            return ConversationHandler.END
        await query.answer()
        value = (query.data or "").partition(":")[2]

        if value == "cancel":
            context.user_data.clear()
            await query.message.reply_text("Радар отменён.", reply_markup=MENU)
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
            self.db.get_project,
            query.from_user.id,
            project_id,
        )
        if not project:
            await query.message.reply_text(
                "Проект не найден или недоступен.",
                reply_markup=MENU,
            )
            return ConversationHandler.END

        context.user_data["radar_project"] = project
        await query.message.reply_text(
            f"📡 Радар для проекта «{project['name']}»\n\n"
            f"Целевые клиенты: "
            f"{project.get('target_audience') or project.get('niche')}\n"
            f"Регион: {project.get('region') or 'Россия'}\n"
            f"Приоритет: {project.get('priorities') or 'не указан'}\n\n"
            "Сколько лидов найти? Введите число от 1 до 5."
        )
        return RADAR_LIMIT

    async def receive_radar_limit(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        try:
            result_limit = int((update.effective_message.text or "").strip())
            if not 1 <= result_limit <= 5:
                raise ValueError
        except ValueError:
            await update.effective_message.reply_text("Введите число от 1 до 5.")
            return RADAR_LIMIT

        project = dict(context.user_data.get("radar_project") or {})
        if not project:
            await update.effective_message.reply_text(
                "Проект не выбран. Откройте «📡 Радары» ещё раз.",
                reply_markup=MENU,
            )
            return ConversationHandler.END

        target = _project_target(project)
        regions = _project_regions(project)
        radar_id = await asyncio.to_thread(
            self.db.create_radar,
            update.effective_user.id,
            [target],
            regions,
            result_limit,
            int(project["id"]),
        )
        context.user_data.clear()
        return await self._run_radar(
            update,
            radar_id=radar_id,
            niches=[target],
            regions=regions,
            result_limit=result_limit,
            saved_now=True,
        )

    async def radar_run(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        if not self.authorized(update):
            return await self.reject(update)
        if len(context.args) != 1:
            await update.effective_message.reply_text(
                "Формат: /radar_run ID",
                reply_markup=MENU,
            )
            return ConversationHandler.END
        try:
            radar_id = int(context.args[0])
        except ValueError:
            await update.effective_message.reply_text(
                "ID радара должен быть числом.",
                reply_markup=MENU,
            )
            return ConversationHandler.END

        radar = await asyncio.to_thread(
            self.db.get_radar,
            update.effective_user.id,
            radar_id,
        )
        if not radar:
            await update.effective_message.reply_text(
                "Радар с таким ID не найден.",
                reply_markup=MENU,
            )
            return ConversationHandler.END

        project_id = radar.get("project_id")
        project = None
        if project_id is not None:
            project = await asyncio.to_thread(
                self.db.get_project,
                update.effective_user.id,
                int(project_id),
            )

        if project:
            niches = [_project_target(project)]
            regions = _project_regions(project)
        else:
            niches = str(radar["niches"]).splitlines()
            regions = str(radar["regions"]).splitlines()

        return await self._run_radar(
            update,
            radar_id=radar_id,
            niches=niches,
            regions=regions,
            result_limit=int(radar["result_limit"]),
            saved_now=False,
        )

    async def run_radar(
        self: Any,
        update: Update,
        *,
        radar_id: int,
        niches: list[str],
        regions: list[str],
        result_limit: int,
        saved_now: bool,
    ) -> int:
        radar = await asyncio.to_thread(
            self.db.get_radar,
            update.effective_user.id,
            radar_id,
        )
        project_id = (
            int(radar["project_id"])
            if radar and radar.get("project_id") is not None
            else None
        )
        project_name = str(radar.get("project_name") or "").strip() if radar else ""

        combinations = [(niche, region) for niche in niches for region in regions][:3]
        action = "создан и запущен" if saved_now else "запущен повторно"
        title = f" для проекта «{project_name}»" if project_name else ""
        await update.effective_message.reply_text(
            f"📡 Радар ID {radar_id}{title} {action}.\n"
            "Проверяю релевантность результатов и отбрасываю статьи, "
            "вакансии, каталоги и посторонние сайты…"
        )

        collected: list[Lead] = []
        try:
            for target, region in combinations:
                leads = await asyncio.to_thread(
                    self.search_client.search,
                    target,
                    region,
                    result_limit,
                )
                if not leads:
                    continue
                ids = await asyncio.to_thread(
                    self.db.save_leads,
                    update.effective_user.id,
                    leads,
                    project_id,
                )
                for lead, lead_id in zip(leads, ids, strict=True):
                    lead.id = lead_id
                collected.extend(leads)
        except Exception:
            logging.exception("Project radar search failed")
            await update.effective_message.reply_text(
                "Поиск радара не завершился. Попробуйте запустить его позже.",
                reply_markup=MENU,
            )
            return ConversationHandler.END

        unique: dict[int, Lead] = {
            int(lead.id): lead for lead in collected if lead.id is not None
        }
        if not unique:
            await update.effective_message.reply_text(
                "Радар отработал, но подходящих лидов не найдено. "
                "Случайные и нерелевантные результаты не были сохранены.",
                reply_markup=MENU,
            )
            return ConversationHandler.END

        visible = list(unique.values())[:10]
        await update.effective_message.reply_text(
            f"✅ Радар завершён. Найдено и сохранено: {len(unique)}\n"
            f"ID радара для повторного запуска: {radar_id}\n\n"
            + self.format_leads(visible),
            reply_markup=MENU,
            disable_web_page_preview=True,
        )
        return ConversationHandler.END

    old_build_application = bot_class.build_application

    @wraps(old_build_application)
    def build_application(self: Any):
        application = old_build_application(self)
        for handlers in application.handlers.values():
            for handler in handlers:
                if not isinstance(handler, ConversationHandler):
                    continue
                if RADAR_NICHES not in handler.states or RADAR_LIMIT not in handler.states:
                    continue
                handler.states[RADAR_NICHES] = [
                    CallbackQueryHandler(
                        self.select_radar_project,
                        pattern=r"^radar_project:(?:\d+|new|cancel)$",
                    )
                ]
                handler.states[RADAR_LIMIT] = [
                    MessageHandler(USER_INPUT_FILTER, self.receive_radar_limit)
                ]
                return application
        raise RuntimeError("Не найден основной ConversationHandler LeadPilot")

    database_class.init_schema = init_schema
    database_class.create_radar = create_radar
    database_class.list_radars = list_radars
    database_class.get_radar = get_radar

    bot_class.radar_start = radar_start
    bot_class.select_radar_project = select_radar_project
    bot_class.receive_radar_limit = receive_radar_limit
    bot_class.radar_run = radar_run
    bot_class._run_radar = run_radar
    bot_class.build_application = build_application
    bot_class._project_radars_installed = True
