from __future__ import annotations

import asyncio
from functools import wraps
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes


def _repair_user_leads(database: Any, user_id: int) -> None:
    """Repair only invalid legacy lead fields without changing valid IDs."""
    select = database._sql(
        """
        SELECT id, user_lead_id
        FROM leads
        WHERE user_id = ?
        ORDER BY id
        """
    )
    clear_id = database._sql(
        "UPDATE leads SET user_lead_id = NULL WHERE id = ? AND user_id = ?"
    )
    set_id = database._sql(
        "UPDATE leads SET user_lead_id = ? WHERE id = ? AND user_id = ?"
    )
    normalize = database._sql(
        """
        UPDATE leads
        SET website = COALESCE(website, ''),
            phone = COALESCE(phone, ''),
            address = COALESCE(address, ''),
            snippet = COALESCE(snippet, ''),
            search_query = COALESCE(search_query, ''),
            score = COALESCE(score, 0),
            status = COALESCE(NULLIF(status, ''), 'new')
        WHERE user_id = ?
        """
    )

    with database._connect() as connection:  # noqa: SLF001
        rows = connection.execute(select, (user_id,)).fetchall()
        used: set[int] = set()
        invalid_row_ids: list[int] = []

        for row in rows:
            row_id = int(row["id"])
            raw = row["user_lead_id"]
            try:
                personal_id = int(raw) if raw is not None else 0
            except (TypeError, ValueError):
                personal_id = 0

            if personal_id <= 0 or personal_id in used:
                invalid_row_ids.append(row_id)
                continue
            used.add(personal_id)

        for row_id in invalid_row_ids:
            connection.execute(clear_id, (row_id, user_id))

        next_id = 1
        for row_id in invalid_row_ids:
            while next_id in used:
                next_id += 1
            connection.execute(set_id, (next_id, row_id, user_id))
            used.add(next_id)
            next_id += 1

        connection.execute(normalize, (user_id,))
        connection.commit()


def install_owner_lead_recovery(
    bot_class: type[Any],
    database_class: type[Any],
) -> None:
    """Make legacy owner lead data compatible with the current lead actions."""
    if getattr(bot_class, "_owner_lead_recovery_installed", False):
        return

    database_class.repair_user_leads = _repair_user_leads

    original_list_leads = bot_class.list_leads
    original_message_start = bot_class.message_start
    original_analyze_start = bot_class.analyze_start
    original_receive_message = bot_class.receive_lead_id
    original_receive_analysis = bot_class.receive_analyze_lead_id

    async def prepare_owner(self: Any, update: Update) -> None:
        user = update.effective_user
        if user is None or not self.is_owner(update):
            return
        await asyncio.to_thread(self.db.repair_user_leads, user.id)

    @wraps(original_list_leads)
    async def list_leads(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> Any:
        await prepare_owner(self, update)
        return await original_list_leads(self, update, context)

    @wraps(original_message_start)
    async def message_start(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> Any:
        await prepare_owner(self, update)
        return await original_message_start(self, update, context)

    @wraps(original_analyze_start)
    async def analyze_start(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> Any:
        await prepare_owner(self, update)
        return await original_analyze_start(self, update, context)

    @wraps(original_receive_message)
    async def receive_lead_id(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> Any:
        await prepare_owner(self, update)
        return await original_receive_message(self, update, context)

    @wraps(original_receive_analysis)
    async def receive_analyze_lead_id(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> Any:
        await prepare_owner(self, update)
        return await original_receive_analysis(self, update, context)

    bot_class.list_leads = list_leads
    bot_class.message_start = message_start
    bot_class.analyze_start = analyze_start
    bot_class.receive_lead_id = receive_lead_id
    bot_class.receive_analyze_lead_id = receive_analyze_lead_id
    bot_class._owner_lead_recovery_installed = True
