from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Any

from telegram import ReplyKeyboardRemove, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import bot as bot_module
from .lead_action_buttons import _visible_words
from .models import Lead


OWNER_ROUTING_GROUP = -20000
OWNER_PENDING_KEY = "_owner_lead_action_pending"
ACTION_ANALYZE = "analyze"
ACTION_MESSAGE = "message"


def _is_owner_account(bot: Any, update: Update) -> bool:
    user = update.effective_user
    if user is None:
        return False
    if bot.settings.owner_telegram_id == user.id:
        return True
    try:
        return str(bot.db.get_role(user.id)) == "owner"
    except Exception:
        return False


def _rows_to_leads(database: Any, rows: list[Any]) -> list[Lead]:
    return [database._row_to_lead(row) for row in rows]  # noqa: SLF001


def _load_owner_leads(database: Any, user_id: int, limit: int = 10) -> list[Lead]:
    """Read owner leads without depending on patched Database methods."""
    personal_statement = database._sql(
        """
        SELECT COALESCE(user_lead_id, id) AS id, name, source_url, website,
               phone, address, snippet, search_query, score,
               COALESCE(NULLIF(status, ''), 'new') AS status
        FROM leads
        WHERE user_id = ?
        ORDER BY COALESCE(user_lead_id, id) DESC
        LIMIT ?
        """
    )
    legacy_statement = database._sql(
        """
        SELECT id, name, source_url, website, phone, address, snippet,
               search_query, score, COALESCE(NULLIF(status, ''), 'new') AS status
        FROM leads
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """
    )
    with database._connect() as connection:  # noqa: SLF001
        try:
            rows = connection.execute(personal_statement, (user_id, limit)).fetchall()
        except Exception:
            rows = connection.execute(legacy_statement, (user_id, limit)).fetchall()
    return _rows_to_leads(database, list(rows))


def _load_owner_lead(database: Any, user_id: int, lead_id: int) -> Lead | None:
    """Resolve both current personal IDs and legacy physical IDs."""
    personal_statement = database._sql(
        """
        SELECT COALESCE(user_lead_id, id) AS id, name, source_url, website,
               phone, address, snippet, search_query, score,
               COALESCE(NULLIF(status, ''), 'new') AS status
        FROM leads
        WHERE user_id = ? AND (user_lead_id = ? OR id = ?)
        ORDER BY CASE WHEN user_lead_id = ? THEN 0 ELSE 1 END
        LIMIT 1
        """
    )
    legacy_statement = database._sql(
        """
        SELECT id, name, source_url, website, phone, address, snippet,
               search_query, score, COALESCE(NULLIF(status, ''), 'new') AS status
        FROM leads
        WHERE user_id = ? AND id = ?
        LIMIT 1
        """
    )
    with database._connect() as connection:  # noqa: SLF001
        try:
            row = connection.execute(
                personal_statement,
                (user_id, lead_id, lead_id, lead_id),
            ).fetchone()
        except Exception:
            row = connection.execute(legacy_statement, (user_id, lead_id)).fetchone()
    return database._row_to_lead(row) if row else None  # noqa: SLF001


def _lead_blocks(bot: Any, leads: list[Lead]) -> list[str]:
    return [bot.format_leads([lead]) for lead in leads]


async def _send_leads(
    bot: Any,
    update: Update,
    leads: list[Lead],
    *,
    suffix: str = "",
    remove_keyboard: bool = False,
) -> None:
    message = update.effective_message
    if message is None:
        return

    chunks: list[str] = []
    current = ""
    for block in _lead_blocks(bot, leads):
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) <= 3300:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = block
    if current:
        chunks.append(current)

    if suffix:
        if chunks and len(chunks[-1]) + len(suffix) + 2 <= 3900:
            chunks[-1] += f"\n\n{suffix}"
        else:
            chunks.append(suffix)

    reply_markup = ReplyKeyboardRemove() if remove_keyboard else bot_module.MENU
    for index, chunk in enumerate(chunks):
        await message.reply_text(
            chunk,
            reply_markup=reply_markup if index == len(chunks) - 1 else None,
            disable_web_page_preview=True,
        )


def _analysis_text(lead: Lead) -> str:
    strengths: list[str] = []
    gaps: list[str] = []
    if lead.website:
        strengths.append("есть сайт")
    else:
        gaps.append("сайт не найден")
    if lead.phone:
        strengths.append("есть публичный телефон")
    else:
        gaps.append("телефон не найден")
    if lead.address:
        strengths.append("есть локальная привязка")
    if lead.score >= 80:
        strengths.append("высокая релевантность")
    elif lead.score < 50:
        gaps.append("низкая релевантность запросу")

    text = (
        f"💎 Анализ клиента · ID {lead.id}\n\n"
        f"Компания: {lead.name}\n"
        f"Рейтинг: {lead.score}/100\n"
        f"Контакт: {lead.contact}\n"
        f"Адрес: {lead.address or 'не найден'}\n"
        f"Описание: {lead.snippet or 'нет данных'}\n\n"
        f"Сильные сигналы: {', '.join(strengths) or 'не обнаружены'}\n"
        f"Что проверить: {', '.join(gaps) or 'критичных пробелов нет'}\n\n"
        "Следующий шаг: проверьте источник и подготовьте персональное "
        "сообщение без массовой рассылки."
    )
    if lead.source_url and not lead.source_url.startswith(("demo://", "serpapi://")):
        text += f"\nИсточник: {lead.source_url}"
    return text


def install_owner_emergency_actions(bot_class: type[Any]) -> None:
    """Give the owner an isolated, unlimited and observable lead-action path."""
    if getattr(bot_class, "_owner_emergency_actions_installed", False):
        return

    original_build_application = bot_class.build_application

    target_actions = {
        _visible_words(bot_module.BUTTON_LEADS): "leads",
        _visible_words(bot_module.BUTTON_ANALYZE): ACTION_ANALYZE,
        _visible_words(bot_module.BUTTON_MESSAGE): ACTION_MESSAGE,
    }
    menu_keys = {_visible_words(button) for button in bot_module.MENU_BUTTONS}

    async def route_owner_lead_actions(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not _is_owner_account(self, update):
            return
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None or not message.text:
            return

        key = _visible_words(message.text)
        pending = context.user_data.get(OWNER_PENDING_KEY)

        try:
            if pending and key in menu_keys:
                context.user_data.pop(OWNER_PENDING_KEY, None)
                pending = None

            if pending in {ACTION_ANALYZE, ACTION_MESSAGE}:
                try:
                    lead_id = int(message.text.strip())
                except ValueError:
                    await message.reply_text("Введите числовой ID.")
                    raise ApplicationHandlerStop()

                lead = await asyncio.to_thread(
                    _load_owner_lead,
                    self.db,
                    user.id,
                    lead_id,
                )
                if lead is None:
                    await message.reply_text(
                        "Лид с таким ID не найден. Попробуйте ещё раз."
                    )
                    raise ApplicationHandlerStop()

                if pending == ACTION_ANALYZE:
                    context.user_data.pop(OWNER_PENDING_KEY, None)
                    await message.reply_text(
                        _analysis_text(lead),
                        reply_markup=bot_module.MENU,
                        disable_web_page_preview=True,
                    )
                    raise ApplicationHandlerStop()

                niche = ""
                if hasattr(self.db, "get_user_niche"):
                    niche = await asyncio.to_thread(self.db.get_user_niche, user.id)
                await message.reply_text("Готовлю черновик обращения…")
                try:
                    outreach = await asyncio.to_thread(
                        self.outreach.generate,
                        lead,
                        niche,
                    )
                except Exception:
                    logging.exception("Owner outreach generation failed")
                    outreach = self.outreach.fallback(lead, niche)
                    outreach += (
                        "\n\nOpenAI временно не ответил, поэтому показан "
                        "базовый черновик."
                    )
                context.user_data.pop(OWNER_PENDING_KEY, None)
                await message.reply_text(outreach, reply_markup=bot_module.MENU)
                raise ApplicationHandlerStop()

            action = target_actions.get(key)
            if action is None:
                return

            context.user_data.clear()
            leads = await asyncio.to_thread(_load_owner_leads, self.db, user.id, 10)

            if action == "leads":
                if not leads:
                    await message.reply_text(
                        "Сохранённых лидов пока нет. Запустите поиск клиентов.",
                        reply_markup=bot_module.MENU,
                    )
                else:
                    await _send_leads(self, update, leads)
                raise ApplicationHandlerStop()

            if not leads:
                empty_text = (
                    "Сначала найдите хотя бы одного клиента."
                    if action == ACTION_ANALYZE
                    else "Сначала найдите и сохраните хотя бы одного лида."
                )
                await message.reply_text(empty_text, reply_markup=bot_module.MENU)
                raise ApplicationHandlerStop()

            if action == ACTION_ANALYZE:
                context.user_data[OWNER_PENDING_KEY] = ACTION_ANALYZE
                await _send_leads(
                    self,
                    update,
                    leads,
                    suffix="Введите ID клиента для подробного анализа.",
                    remove_keyboard=True,
                )
                raise ApplicationHandlerStop()

            context.user_data[OWNER_PENDING_KEY] = ACTION_MESSAGE
            await _send_leads(
                self,
                update,
                leads,
                suffix="Введите ID лида, для которого подготовить сообщение.",
                remove_keyboard=True,
            )
            raise ApplicationHandlerStop()

        except ApplicationHandlerStop:
            raise
        except Exception as exc:
            logging.exception("Owner emergency lead action failed")
            context.user_data.pop(OWNER_PENDING_KEY, None)
            await message.reply_text(
                "⚠️ Функция была запущена, но внутри возникла ошибка.\n\n"
                f"Причина: {type(exc).__name__}: {str(exc)[:500]}\n\n"
                "Отправьте этот текст в поддержку — теперь ошибка больше не скрывается.",
                reply_markup=bot_module.MENU,
            )
            raise ApplicationHandlerStop()

    async def reset_owner_state(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not _is_owner_account(self, update):
            return
        context.user_data.clear()
        text = (update.effective_message.text or "").split(maxsplit=1)[0].lower()
        if text.startswith("/start"):
            await self.start(update, context)
        elif text.startswith("/menu"):
            await self.menu(update, context)
        else:
            await self.cancel(update, context)
        raise ApplicationHandlerStop()

    @wraps(original_build_application)
    def build_application(self: Any):
        application = original_build_application(self)
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.route_owner_lead_actions,
            ),
            group=OWNER_ROUTING_GROUP,
        )
        application.add_handler(
            CommandHandler(
                ("start", "menu", "cancel"),
                self.reset_owner_lead_state,
            ),
            group=OWNER_ROUTING_GROUP,
        )
        return application

    bot_class.route_owner_lead_actions = route_owner_lead_actions
    bot_class.reset_owner_lead_state = reset_owner_state
    bot_class.build_application = build_application
    bot_class._owner_emergency_actions_installed = True
