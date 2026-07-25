from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Any

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler

from . import scheduled_radars as scheduled
from .bot import MENU, RADAR_LIMIT, RADAR_NICHES
from .project_radars import _project_regions, _project_target

HIDDEN_RADAR_COMMANDS = {"radar_run", "radar_stop", "radar_resume"}


def _active_keyboard(radar_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔍 Проверить сейчас",
                    callback_data=f"radar_control:run:{radar_id}",
                ),
                InlineKeyboardButton(
                    "⏸ Остановить",
                    callback_data=f"radar_control:stop:{radar_id}",
                ),
            ]
        ]
    )


def _stopped_keyboard(radar_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "▶️ Возобновить",
                    callback_data=f"radar_control:resume:{radar_id}",
                )
            ]
        ]
    )


def _controls(radar_id: int, active: bool) -> InlineKeyboardMarkup:
    return _active_keyboard(radar_id) if active else _stopped_keyboard(radar_id)


def _radar_card(radar: dict[str, Any]) -> str:
    active = bool(int(radar.get("active") or 0))
    status = "🟢 Активен" if active else "⏸ Остановлен"
    next_run = (
        scheduled._next_run_text(radar.get("next_run_at"))
        if active
        else "—"
    )
    return (
        "📡 Автоматический радар\n\n"
        f"ID: {radar['id']}\n"
        f"Проект: {radar.get('project_name') or 'Проект'}\n"
        f"Статус: {status}\n"
        f"Период: {scheduled._interval_label(int(radar['interval_hours']))}\n"
        f"Лидов за запуск: до {int(radar.get('result_limit') or 1)}\n"
        f"Следующий запуск: {next_run}"
    )


async def _resource_available(
    owner: Any,
    update: Update,
    field: str,
) -> bool:
    owner.ensure_account(update)
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return False
    snapshot = await asyncio.to_thread(owner.db.get_usage_snapshot, user.id)
    if snapshot.get("unlimited"):
        return True
    if not snapshot.get("active"):
        await owner.reject(update)
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


async def _send_status(
    application: Any,
    chat_id: int,
    text: str,
    *,
    radar_id: int,
    active: bool,
) -> None:
    try:
        await application.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=_controls(radar_id, active),
        )
    except Exception:
        logging.exception("Failed to send radar status with controls")


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
        scheduled._deactivate_radar(owner.db, radar_id, "Проект не привязан")
        await _send_status(
            application,
            chat_id,
            f"⏸ Радар ID {radar_id} остановлен: к нему не привязан проект.",
            radar_id=radar_id,
            active=False,
        )
        return 0

    project = await asyncio.to_thread(
        owner.db.get_project,
        user_id,
        int(project_id),
    )
    if not project:
        scheduled._deactivate_radar(
            owner.db,
            radar_id,
            "Проект удалён или недоступен",
        )
        await _send_status(
            application,
            chat_id,
            f"⏸ Радар ID {radar_id} остановлен: проект больше недоступен.",
            radar_id=radar_id,
            active=False,
        )
        return 0

    snapshot = await asyncio.to_thread(owner.db.get_usage_snapshot, user_id)
    if not snapshot.get("active"):
        scheduled._deactivate_radar(owner.db, radar_id, "Нет активного доступа")
        await _send_status(
            application,
            chat_id,
            f"⏸ Радар ID {radar_id} остановлен: доступ к боту закончился.",
            radar_id=radar_id,
            active=False,
        )
        return 0

    if not snapshot.get("unlimited"):
        if snapshot.get("remaining", {}).get("searches", 0) <= 0:
            scheduled._deactivate_radar(
                owner.db,
                radar_id,
                "Закончился лимит поисков",
            )
            await _send_status(
                application,
                chat_id,
                f"⏸ Радар ID {radar_id} остановлен: закончился лимит поисков.",
                radar_id=radar_id,
                active=False,
            )
            return 0
        if snapshot.get("remaining", {}).get("leads", 0) <= 0:
            scheduled._deactivate_radar(
                owner.db,
                radar_id,
                "Закончился лимит лидов",
            )
            await _send_status(
                application,
                chat_id,
                f"⏸ Радар ID {radar_id} остановлен: закончился лимит лидов.",
                radar_id=radar_id,
                active=False,
            )
            return 0

    ok, _ = await asyncio.to_thread(
        owner.db.consume_usage,
        user_id,
        "searches",
        1,
    )
    if not ok:
        scheduled._deactivate_radar(
            owner.db,
            radar_id,
            "Не удалось списать поиск",
        )
        return 0

    target = _project_target(project)
    regions = _project_regions(project)
    result_limit = max(1, min(int(radar.get("result_limit") or 5), 10))
    candidate_limit = min(max(result_limit * 3, 10), 20)
    candidates: dict[str, Any] = {}

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
        await asyncio.to_thread(
            owner.db.refund_usage,
            user_id,
            "searches",
            1,
        )
        scheduled._mark_radar_error(owner.db, radar_id, str(exc))
        if manual:
            current = await asyncio.to_thread(
                owner.db.get_scheduled_radar,
                user_id,
                radar_id,
            )
            await _send_status(
                application,
                chat_id,
                f"Не удалось проверить радар ID {radar_id}. Попробуйте позже.",
                radar_id=radar_id,
                active=bool(current and int(current.get("active") or 0)),
            )
        return 0

    leads = list(candidates.values())
    existing = await asyncio.to_thread(
        scheduled._existing_sources,
        owner.db,
        user_id,
        leads,
    )
    new_leads = [
        lead for lead in leads if lead.source_url not in existing
    ][:result_limit]

    current = await asyncio.to_thread(
        owner.db.get_scheduled_radar,
        user_id,
        radar_id,
    )
    active = bool(current and int(current.get("active") or 0))

    if not new_leads:
        await asyncio.to_thread(
            owner.db.refund_usage,
            user_id,
            "searches",
            1,
        )
        scheduled._mark_radar_error(owner.db, radar_id, "")
        if manual:
            await _send_status(
                application,
                chat_id,
                f"📡 Радар ID {radar_id}: новых подходящих лидов пока нет.",
                radar_id=radar_id,
                active=active,
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

    scheduled._mark_radar_error(owner.db, radar_id, "")
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
            reply_markup=_controls(radar_id, active),
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logging.exception("Failed to deliver scheduled radar leads")
        scheduled._mark_radar_error(
            owner.db,
            radar_id,
            f"Ошибка отправки: {exc}",
        )
    return len(new_leads)


def install_radar_inline_controls(bot_class: type[Any]) -> None:
    """Replace visible radar commands with inline buttons."""
    if getattr(bot_class, "_radar_inline_controls_installed", False):
        return

    scheduled._execute_scheduled_radar = _execute_scheduled_radar

    async def radar_start(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        if not self.authorized(update):
            return await self.reject(update)
        context.user_data.clear()
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return ConversationHandler.END

        projects, radars = await asyncio.gather(
            asyncio.to_thread(self.db.list_projects, user.id, 20),
            asyncio.to_thread(self.db.list_scheduled_radars, user.id, 20),
        )
        text = (
            "📡 Автоматические радары\n\n"
            "Выберите проект, по которому бот будет автоматически искать "
            "новых лидов и присылать их в этот чат."
        )
        if not projects:
            text += "\n\nПроектов пока нет. Нажмите «➕ Новый проект»."
        await message.reply_text(
            text,
            reply_markup=scheduled._projects_keyboard(projects),
        )

        if radars:
            await message.reply_text("Ваши радары:")
            for radar in radars:
                active = bool(int(radar.get("active") or 0))
                await message.reply_text(
                    _radar_card(radar),
                    reply_markup=_controls(int(radar["id"]), active),
                )
        return RADAR_NICHES

    async def receive_limit(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        if message is None or user is None or chat is None:
            return ConversationHandler.END
        try:
            result_limit = int((message.text or "").strip())
            if not 1 <= result_limit <= 5:
                raise ValueError
        except ValueError:
            await message.reply_text("Введите число от 1 до 5.")
            return RADAR_LIMIT

        project = dict(context.user_data.get("scheduled_radar_project") or {})
        interval_hours = int(
            context.user_data.get("scheduled_radar_interval_hours") or 0
        )
        if not project or not interval_hours:
            context.user_data.clear()
            await message.reply_text(
                "Настройки радара потеряны. Откройте «📡 Радары» ещё раз.",
                reply_markup=MENU,
            )
            return ConversationHandler.END
        if not await _resource_available(self, update, "radars"):
            context.user_data.clear()
            return ConversationHandler.END

        ok, snapshot = await asyncio.to_thread(
            self.db.consume_usage,
            user.id,
            "radars",
            1,
        )
        if not ok:
            context.user_data.clear()
            await message.reply_text(
                "⛔ Не удалось создать радар: лимит радаров исчерпан.",
                reply_markup=MENU,
            )
            return ConversationHandler.END

        try:
            radar_id, _ = await asyncio.to_thread(
                self.db.create_scheduled_radar,
                user_id=user.id,
                chat_id=chat.id,
                project_id=int(project["id"]),
                target=_project_target(project),
                regions=_project_regions(project),
                result_limit=result_limit,
                interval_hours=interval_hours,
            )
            radar = await asyncio.to_thread(
                self.db.get_scheduled_radar,
                user.id,
                radar_id,
            )
        except Exception:
            logging.exception("Scheduled radar creation failed")
            if not snapshot.get("unlimited"):
                await asyncio.to_thread(
                    self.db.refund_usage,
                    user.id,
                    "radars",
                    1,
                )
            context.user_data.clear()
            await message.reply_text(
                "Не удалось сохранить радар. Попробуйте ещё раз.",
                reply_markup=MENU,
            )
            return ConversationHandler.END

        context.user_data.clear()
        if radar is None:
            await message.reply_text(
                "Радар создан, но не удалось открыть его карточку.",
                reply_markup=MENU,
            )
            return ConversationHandler.END
        await message.reply_text(
            "✅ Радар создан. Он будет автоматически присылать только новых "
            "релевантных лидов.",
        )
        await message.reply_text(
            _radar_card(radar),
            reply_markup=_active_keyboard(radar_id),
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

    async def _stop(self: Any, user_id: int, radar_id: int):
        return await asyncio.to_thread(
            self.db.set_scheduled_radar_active,
            user_id,
            radar_id,
            False,
        )

    async def _resume(
        self: Any,
        update: Update,
        user_id: int,
        radar_id: int,
    ):
        if not await _resource_available(self, update, "searches"):
            return None
        if not await _resource_available(self, update, "leads"):
            return None
        return await asyncio.to_thread(
            self.db.set_scheduled_radar_active,
            user_id,
            radar_id,
            True,
        )

    async def radar_stop(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        radar_id = _command_id(context)
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        if radar_id is None:
            await message.reply_text("Не удалось определить радар.", reply_markup=MENU)
            return
        radar = await _stop(self, user.id, radar_id)
        if radar is None:
            await message.reply_text("Радар с таким ID не найден.", reply_markup=MENU)
            return
        await message.reply_text(
            _radar_card(radar),
            reply_markup=_stopped_keyboard(radar_id),
        )

    async def radar_resume(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        radar_id = _command_id(context)
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        if radar_id is None:
            await message.reply_text("Не удалось определить радар.", reply_markup=MENU)
            return
        radar = await _resume(self, update, user.id, radar_id)
        if radar is None:
            return
        await message.reply_text(
            _radar_card(radar),
            reply_markup=_active_keyboard(radar_id),
        )

    async def radar_run(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        radar_id = _command_id(context)
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        if message is None or user is None or chat is None:
            return ConversationHandler.END
        if radar_id is None:
            await message.reply_text("Не удалось определить радар.", reply_markup=MENU)
            return ConversationHandler.END
        radar = await asyncio.to_thread(
            self.db.get_scheduled_radar,
            user.id,
            radar_id,
        )
        if not radar:
            await message.reply_text("Радар с таким ID не найден.", reply_markup=MENU)
            return ConversationHandler.END
        radar["chat_id"] = chat.id
        await message.reply_text(f"📡 Проверяю радар ID {radar_id} прямо сейчас…")
        await _execute_scheduled_radar(
            self,
            context.application,
            radar,
            manual=True,
        )
        return ConversationHandler.END

    async def control_callback(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        query = update.callback_query
        user = update.effective_user
        if query is None or user is None:
            return
        try:
            _, action, raw_id = (query.data or "").split(":", 2)
            radar_id = int(raw_id)
        except (ValueError, TypeError):
            await query.answer("Не удалось определить радар.", show_alert=True)
            return

        radar = await asyncio.to_thread(
            self.db.get_scheduled_radar,
            user.id,
            radar_id,
        )
        if radar is None:
            await query.answer("Радар не найден или принадлежит другому пользователю.", show_alert=True)
            return

        if action == "run":
            await query.answer("Запускаю проверку…")
            radar["chat_id"] = update.effective_chat.id
            if query.message is not None:
                await query.message.reply_text(
                    f"📡 Проверяю радар ID {radar_id} прямо сейчас…"
                )
            await _execute_scheduled_radar(
                self,
                context.application,
                radar,
                manual=True,
            )
            return

        if action == "stop":
            radar = await _stop(self, user.id, radar_id)
            if radar is None:
                await query.answer("Радар не найден.", show_alert=True)
                return
            await query.answer("Радар остановлен")
        elif action == "resume":
            await query.answer()
            radar = await _resume(self, update, user.id, radar_id)
            if radar is None:
                return
        else:
            await query.answer("Неизвестное действие.", show_alert=True)
            return

        active = bool(int(radar.get("active") or 0))
        if query.message is not None and (query.message.text or "").startswith(
            "📡 Автоматический радар"
        ):
            await query.edit_message_text(
                _radar_card(radar),
                reply_markup=_controls(radar_id, active),
            )
            return

        if query.message is not None:
            await query.edit_message_reply_markup(
                reply_markup=_controls(radar_id, active)
            )
            await query.message.reply_text(
                _radar_card(radar),
                reply_markup=_controls(radar_id, active),
            )

    async def show_help(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        if not self.authorized(update):
            await self.reject(update)
            return
        await update.effective_message.reply_text(
            "Команды:\n"
            "/start — приветствие и меню\n"
            "/menu — главное меню\n"
            "/find — найти клиентов\n"
            "/projects — мои проекты\n"
            "/leads — последние лиды\n"
            "/message — создать обращение\n"
            "/radars — автоматические радары\n"
            "/export — выгрузить CSV\n"
            "/analytics — аналитика\n"
            "/role — роль аккаунта\n"
            "/status — проверить работу бота\n"
            "/cancel — отменить текущий шаг\n\n"
            f"Поддержка: {self.settings.support_username}",
            reply_markup=MENU,
        )

    bot_class.radar_start = radar_start
    bot_class.receive_radar_limit = receive_limit
    bot_class.radar_stop = radar_stop
    bot_class.radar_resume = radar_resume
    bot_class.radar_run = radar_run
    bot_class.radar_control_callback = control_callback
    bot_class.show_help = show_help

    old_build_application = bot_class.build_application

    @wraps(old_build_application)
    def build_application(self: Any):
        application = old_build_application(self)
        application.add_handler(
            CallbackQueryHandler(
                self.radar_control_callback,
                pattern=r"^radar_control:(?:run|stop|resume):\d+$",
            ),
            group=-1,
        )

        previous_post_init = application.post_init

        async def post_init(app: Any) -> None:
            if previous_post_init is not None:
                await previous_post_init(app)
            try:
                commands = await app.bot.get_my_commands()
                visible = [
                    BotCommand(item.command, item.description)
                    for item in commands
                    if item.command.lower() not in HIDDEN_RADAR_COMMANDS
                ]
                await app.bot.set_my_commands(visible)

                owner_id = self.settings.owner_telegram_id
                if owner_id is not None:
                    scope = BotCommandScopeChat(chat_id=owner_id)
                    owner_commands = await app.bot.get_my_commands(scope=scope)
                    owner_visible = [
                        BotCommand(item.command, item.description)
                        for item in owner_commands
                        if item.command.lower() not in HIDDEN_RADAR_COMMANDS
                    ]
                    await app.bot.set_my_commands(owner_visible, scope=scope)
            except Exception:
                logging.exception("Failed to hide radar management commands")

        application.post_init = post_init
        return application

    bot_class.build_application = build_application
    bot_class._radar_inline_controls_installed = True
