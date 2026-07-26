from __future__ import annotations

import asyncio
from functools import wraps
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from . import bot as bot_module


TRIAL_LIMITS = (10, 10, 10, 10, 0)


def _used(snapshot: dict[str, Any], field: str) -> int:
    return int(snapshot.get("used", {}).get(field, 0))


async def _snapshot(owner: Any, user_id: int) -> dict[str, Any]:
    return await asyncio.to_thread(owner.db.get_usage_snapshot, user_id)


async def _ensure_spent(
    owner: Any,
    user_id: int,
    field: str,
    before: dict[str, Any],
) -> None:
    if before.get("unlimited") or not before.get("active"):
        return
    after = await _snapshot(owner, user_id)
    if _used(after, field) > _used(before, field):
        return
    await asyncio.to_thread(owner.db.consume_usage, user_id, field, 1)


def install_trial_limits_guard(bot_class: type[Any]) -> None:
    """Set trial totals to ten and guarantee one charge per successful action."""
    if getattr(bot_class, "_trial_limits_guard_installed", False):
        return

    # PLAN_LIMITS is imported by reference in usage_limits, so mutating the
    # existing dictionary updates both enforcement and the /limits display.
    bot_module.PLAN_LIMITS["trial"] = TRIAL_LIMITS
    bot_module.LIVE_TARIFFS_TEXT = bot_module.LIVE_TARIFFS_TEXT.replace(
        "20 поисков · 20 лидов · 20 анализов · 20 сообщений",
        "10 поисков · 10 лидов · 10 анализов · 10 сообщений",
    )

    old_search = bot_class._search_and_reply
    old_analysis = bot_class.receive_analyze_lead_id
    old_message = bot_class.receive_lead_id

    @wraps(old_search)
    async def search(
        self: Any,
        update: Update,
        niche: str,
        region: str,
        limit: int,
        *,
        project_id: int | None = None,
    ):
        user = update.effective_user
        before = await _snapshot(self, user.id) if user else {}
        result = await old_search(
            self,
            update,
            niche,
            region,
            limit,
            project_id=project_id,
        )
        if user and result:
            await _ensure_spent(self, user.id, "searches", before)
        return result

    @wraps(old_analysis)
    async def analysis(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        user = update.effective_user
        before = await _snapshot(self, user.id) if user else {}
        result = await old_analysis(self, update, context)
        if user and result == ConversationHandler.END:
            await _ensure_spent(self, user.id, "analyses", before)
        return result

    @wraps(old_message)
    async def message(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        user = update.effective_user
        before = await _snapshot(self, user.id) if user else {}
        result = await old_message(self, update, context)
        if user and result == ConversationHandler.END:
            await _ensure_spent(self, user.id, "messages", before)
        return result

    bot_class._search_and_reply = search
    bot_class.receive_analyze_lead_id = analysis
    bot_class.receive_lead_id = message
    bot_class._trial_limits_guard_installed = True
