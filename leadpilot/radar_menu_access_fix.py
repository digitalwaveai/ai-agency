from __future__ import annotations

import asyncio
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from .bot import RADAR_NICHES
from .scheduled_radars import _interval_label, _next_run_text, _projects_keyboard


def install_radar_menu_access_fix(bot_class: type[Any]) -> None:
    """Allow users to manage existing radars even when creation quota is spent."""
    if getattr(bot_class, "_radar_menu_access_fix_installed", False):
        return

    async def radar_start(
        self: Any, update: Update, context: ContextTypes.DEFAULT_TYPE
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
            asyncio.to_thread(self.db.list_scheduled_radars, user.id, 10),
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
                active = bool(int(radar.get("active") or 0))
                status = "активен" if active else "остановлен"
                next_text = _next_run_text(radar.get("next_run_at")) if active else "—"
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
            lines.extend(["", "Проектов пока нет. Нажмите «➕ Новый проект»."])
        await message.reply_text(
            "\n".join(lines),
            reply_markup=_projects_keyboard(projects),
        )
        return RADAR_NICHES

    bot_class.radar_start = radar_start
    bot_class._radar_menu_access_fix_installed = True
