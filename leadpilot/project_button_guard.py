from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from .bot import MENU, SEARCH_PROJECT, _project_search_keyboard


def _project_blocks(projects: list[dict[str, Any]]) -> list[str]:
    blocks: list[str] = []
    for project in projects:
        answered = sum(
            bool(str(project.get(field) or "").strip())
            for field in (
                "name",
                "niche",
                "offer",
                "target_audience",
                "region",
                "advantage",
                "priorities",
                "exclusions",
            )
        )
        if not str(project.get("exclusions") or "").strip():
            answered = min(8, answered + 1)
        blocks.append(
            f"📁 {project['name']}\n"
            f"Направление: {project.get('category_name') or 'Своя ниша'}\n"
            f"Ниша: {project.get('niche') or 'не указана'}\n"
            f"Приоритет: {project.get('priorities') or 'не указан'}\n"
            f"Статус: ✅ Активен\n"
            f"Анкета: {answered} / 8\n"
            f"ID проекта: {project['id']}"
        )
    return blocks


async def _send_projects(message: Any, projects: list[dict[str, Any]]) -> None:
    chunks: list[str] = []
    current = "📁 Мои проекты"
    for block in _project_blocks(projects):
        candidate = f"{current}\n\n{block}"
        if len(candidate) <= 3800:
            current = candidate
            continue
        chunks.append(current)
        current = f"📁 Мои проекты — продолжение\n\n{block}"
    chunks.append(current)
    for chunk in chunks:
        await message.reply_text(chunk, reply_markup=MENU)


def install_project_button_guard(bot_class: type[Any]) -> None:
    """Keep project buttons responsive without changing lead search itself."""
    if getattr(bot_class, "_project_button_guard_installed", False):
        return

    old_list_projects = bot_class.list_projects
    old_find_start = bot_class.find_start

    @wraps(old_list_projects)
    async def list_projects(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        try:
            await old_list_projects(self, update, context)
            return
        except Exception:
            logging.exception("Primary project-list handler failed; using fallback")

        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        try:
            if not self.authorized(update):
                await self.reject(update)
                return
            projects = await asyncio.to_thread(self.db.list_projects, user.id, 20)
            if not projects:
                await message.reply_text(
                    "Проектов пока нет. Нажмите «➕ Новый проект».",
                    reply_markup=MENU,
                )
                return
            await _send_projects(message, projects)
        except Exception:
            logging.exception("Fallback project-list handler failed")
            await message.reply_text(
                "Не удалось открыть проекты. Ошибка уже записана в логах Railway.",
                reply_markup=MENU,
            )

    @wraps(old_find_start)
    async def find_start(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        try:
            return await old_find_start(self, update, context)
        except Exception:
            logging.exception("Primary project-search entry failed; using fallback")

        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return ConversationHandler.END
        try:
            if not self.authorized(update):
                return await self.reject(update)
            context.user_data.clear()
            projects = await asyncio.to_thread(self.db.list_projects, user.id, 20)
            if not projects:
                await message.reply_text(
                    "🔎 Найти клиентов\n\n"
                    "Сначала создайте проект — поиск запускается только из "
                    "сохранённого проекта.",
                    reply_markup=MENU,
                )
                return ConversationHandler.END
            await message.reply_text(
                "🔎 Найти клиентов\n\nИз какого проекта найти клиентов?",
                reply_markup=_project_search_keyboard(projects),
            )
            return SEARCH_PROJECT
        except Exception:
            logging.exception("Fallback project-search entry failed")
            await message.reply_text(
                "Не удалось открыть выбор проекта. Ошибка уже записана в логах Railway.",
                reply_markup=MENU,
            )
            return ConversationHandler.END

    bot_class.list_projects = list_projects
    bot_class.find_start = find_start
    bot_class._project_button_guard_installed = True
