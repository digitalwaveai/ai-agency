from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from functools import wraps
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from .bot import MENU, PLAN_LIMITS, ROLE_LABELS
from .models import Lead

FIELDS = ("searches", "leads", "analyses", "messages", "radars")
LABELS = {
    "searches": "поисков",
    "leads": "лидов",
    "analyses": "анализов",
    "messages": "сообщений",
    "radars": "радаров",
}
UNLIMITED = {"owner", "admin", "beta_tester"}
LOCK = threading.RLock()


def _key(access: dict[str, Any]) -> str:
    end = access.get("ends_at")
    end = end.isoformat(timespec="seconds") if isinstance(end, datetime) else str(end or "")
    return f"{access.get('source', '')}:{access.get('plan_code', '')}:{end}"


def _totals(access: dict[str, Any]) -> dict[str, int]:
    raw = PLAN_LIMITS.get(str(access.get("plan_code") or ""), (0, 0, 0, 0, 0))
    return dict(zip(FIELDS, map(int, raw), strict=True))


def _schema(db: Any) -> None:
    with db._connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_counters (
                user_id BIGINT NOT NULL,
                period_key TEXT NOT NULL,
                searches INTEGER NOT NULL DEFAULT 0,
                leads INTEGER NOT NULL DEFAULT 0,
                analyses INTEGER NOT NULL DEFAULT 0,
                messages INTEGER NOT NULL DEFAULT 0,
                radars INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, period_key)
            )
            """
        )
        connection.commit()


def _initial(db: Any, user_id: int, access: dict[str, Any]) -> dict[str, int]:
    values = dict.fromkeys(FIELDS, 0)
    if access.get("source") != "trial":
        return values
    totals = _totals(access)
    with db._connect() as connection:
        leads = connection.execute(
            db._sql("SELECT COUNT(*) AS total FROM leads WHERE user_id = ?"),
            (user_id,),
        ).fetchone()
        radars = connection.execute(
            db._sql("SELECT COUNT(*) AS total FROM radars WHERE user_id = ?"),
            (user_id,),
        ).fetchone()
    values["leads"] = min(int(leads["total"] or 0), totals["leads"])
    values["radars"] = min(int(radars["total"] or 0), totals["radars"])
    return values


def _row(db: Any, user_id: int, access: dict[str, Any]) -> tuple[str, Any]:
    period = _key(access)
    initial = _initial(db, user_id, access)
    insert = db._sql(
        """
        INSERT INTO usage_counters
            (user_id, period_key, searches, leads, analyses, messages, radars)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, period_key) DO NOTHING
        """
    )
    select = db._sql(
        """
        SELECT searches, leads, analyses, messages, radars
        FROM usage_counters WHERE user_id = ? AND period_key = ?
        """
    )
    with db._connect() as connection:
        connection.execute(
            insert,
            (user_id, period, *(initial[field] for field in FIELDS)),
        )
        row = connection.execute(select, (user_id, period)).fetchone()
        connection.commit()
    return period, row


def _snapshot(db: Any, user_id: int) -> dict[str, Any]:
    role = db.get_role(user_id)
    if role in UNLIMITED:
        return {"active": True, "unlimited": True, "role": role}
    access = db.get_access_state(user_id)
    if not access.get("active"):
        return {"active": False, "unlimited": False, "role": role, "access": access}
    period, row = _row(db, user_id, access)
    totals = _totals(access)
    used = {field: int(row[field] or 0) for field in FIELDS}
    remaining = {field: max(totals[field] - used[field], 0) for field in FIELDS}
    return {
        "active": True,
        "unlimited": False,
        "role": role,
        "access": access,
        "period_key": period,
        "totals": totals,
        "used": used,
        "remaining": remaining,
    }


def _change(db: Any, user_id: int, period: str, field: str, delta: int) -> None:
    if field not in FIELDS or not delta:
        return
    if delta > 0:
        assignment = f"{field} = {field} + ?"
        params = (delta, user_id, period)
    else:
        assignment = f"{field} = CASE WHEN {field} + ? < 0 THEN 0 ELSE {field} + ? END"
        params = (delta, delta, user_id, period)
    with db._connect() as connection:
        connection.execute(
            db._sql(
                f"UPDATE usage_counters SET {assignment}, updated_at = CURRENT_TIMESTAMP "
                "WHERE user_id = ? AND period_key = ?"
            ),
            params,
        )
        connection.commit()


def _consume(db: Any, user_id: int, field: str, amount: int = 1):
    if field not in FIELDS or amount <= 0:
        raise ValueError("Invalid usage resource")
    with LOCK:
        snap = _snapshot(db, user_id)
        if snap.get("unlimited"):
            return True, snap
        if not snap.get("active") or snap["remaining"][field] < amount:
            return False, snap
        _change(db, user_id, snap["period_key"], field, amount)
        return True, _snapshot(db, user_id)


def _refund(db: Any, user_id: int, field: str, amount: int = 1) -> None:
    with LOCK:
        snap = _snapshot(db, user_id)
        if snap.get("unlimited") or not snap.get("active"):
            return
        _change(db, user_id, snap["period_key"], field, -amount)


def _existing(db: Any, user_id: int, leads: list[Lead]) -> set[str]:
    statement = db._sql("SELECT source_url FROM leads WHERE user_id = ? AND source_url = ?")
    result: set[str] = set()
    with db._connect() as connection:
        for source in dict.fromkeys(lead.source_url for lead in leads):
            row = connection.execute(statement, (user_id, source)).fetchone()
            if row:
                result.add(str(row["source_url"]))
    return result


def install_usage_limits(bot_class: type[Any], database_class: type[Any]) -> None:
    if getattr(database_class, "_usage_limits_installed", False):
        return

    old_init = database_class.init_schema
    old_save = database_class.save_leads

    @wraps(old_init)
    def init_schema(self):
        old_init(self)
        _schema(self)

    @wraps(old_save)
    def save_leads(self, user_id, leads, project_id=None):
        if self.get_role(user_id) in UNLIMITED:
            return old_save(self, user_id, leads, project_id)
        with LOCK:
            snap = _snapshot(self, user_id)
            if not snap.get("active"):
                leads.clear()
                return []
            remaining = snap["remaining"]["leads"]
            existing = _existing(self, user_id, leads)
            allowed: list[Lead] = []
            new_sources: set[str] = set()
            for lead in leads:
                source = lead.source_url
                if source in existing or source in new_sources:
                    allowed.append(lead)
                elif len(new_sources) < remaining:
                    new_sources.add(source)
                    allowed.append(lead)
            if not allowed:
                leads.clear()
                return []
            ids = old_save(self, user_id, allowed, project_id)
            leads[:] = allowed
            if new_sources:
                _change(self, user_id, snap["period_key"], "leads", len(new_sources))
            return ids

    database_class.init_schema = init_schema
    database_class.save_leads = save_leads
    database_class.get_usage_snapshot = lambda self, user_id: _snapshot(self, user_id)
    database_class.consume_usage = lambda self, user_id, field, amount=1: _consume(
        self, user_id, field, amount
    )
    database_class.refund_usage = lambda self, user_id, field, amount=1: _refund(
        self, user_id, field, amount
    )
    database_class._usage_limits_installed = True

    old_find = bot_class.find_start
    old_search = bot_class._search_and_reply
    old_message_start = bot_class.message_start
    old_message = bot_class.receive_lead_id
    old_analyze_start = bot_class.analyze_start
    old_analyze = bot_class.receive_analyze_lead_id
    old_radar_start = bot_class.radar_start
    old_radar_limit = bot_class.receive_radar_limit
    old_radar_run = bot_class.radar_run
    old_run_radar = bot_class._run_radar

    async def denied(self, update, field, snap):
        message = update.effective_message
        if not message:
            return
        total = snap.get("totals", {}).get(field, 0)
        text = (
            f"⛔ В вашем тарифе нет доступных {LABELS[field]}."
            if total == 0
            else f"⛔ Лимит {LABELS[field]} исчерпан."
        )
        await message.reply_text(
            text + "\n\nОткройте «⭐ Тарифы», чтобы увеличить лимиты.",
            reply_markup=MENU,
        )

    async def available(self, update, field):
        self.ensure_account(update)
        user = update.effective_user
        if not user:
            return False, {}
        snap = await asyncio.to_thread(self.db.get_usage_snapshot, user.id)
        if snap.get("unlimited"):
            return True, snap
        if not snap.get("active"):
            await self.reject(update)
            return False, snap
        if snap["remaining"].get(field, 0) <= 0:
            await denied(self, update, field, snap)
            return False, snap
        return True, snap

    @wraps(old_find)
    async def find_start(self, update, context):
        if not (await available(self, update, "searches"))[0]:
            return ConversationHandler.END
        if not (await available(self, update, "leads"))[0]:
            return ConversationHandler.END
        return await old_find(self, update, context)

    @wraps(old_search)
    async def search(self, update, niche, region, limit, *, project_id=None):
        user = update.effective_user
        if not user or not (await available(self, update, "leads"))[0]:
            return []
        ok, snap = await asyncio.to_thread(self.db.consume_usage, user.id, "searches", 1)
        if not ok:
            await denied(self, update, "searches", snap)
            return []
        return await old_search(
            self, update, niche, region, limit, project_id=project_id
        )

    @wraps(old_message_start)
    async def message_start(self, update, context):
        if not (await available(self, update, "messages"))[0]:
            return ConversationHandler.END
        return await old_message_start(self, update, context)

    @wraps(old_message)
    async def message(self, update, context):
        if not (await available(self, update, "messages"))[0]:
            return ConversationHandler.END
        result = await old_message(self, update, context)
        if result == ConversationHandler.END and update.effective_user:
            await asyncio.to_thread(
                self.db.consume_usage, update.effective_user.id, "messages", 1
            )
        return result

    @wraps(old_analyze_start)
    async def analyze_start(self, update, context):
        if not (await available(self, update, "analyses"))[0]:
            return ConversationHandler.END
        return await old_analyze_start(self, update, context)

    @wraps(old_analyze)
    async def analyze(self, update, context):
        if not (await available(self, update, "analyses"))[0]:
            return ConversationHandler.END
        result = await old_analyze(self, update, context)
        if result == ConversationHandler.END and update.effective_user:
            await asyncio.to_thread(
                self.db.consume_usage, update.effective_user.id, "analyses", 1
            )
        return result

    @wraps(old_radar_start)
    async def radar_start(self, update, context):
        for field in ("radars", "searches", "leads"):
            if not (await available(self, update, field))[0]:
                return ConversationHandler.END
        return await old_radar_start(self, update, context)

    @wraps(old_radar_limit)
    async def radar_limit(self, update, context):
        user = update.effective_user
        message = update.effective_message
        if not user or not message:
            return ConversationHandler.END
        try:
            valid = 1 <= int((message.text or "").strip()) <= 5
        except ValueError:
            valid = False
        if not valid:
            return await old_radar_limit(self, update, context)
        if not (await available(self, update, "searches"))[0]:
            return ConversationHandler.END
        ok, snap = await asyncio.to_thread(self.db.consume_usage, user.id, "radars", 1)
        if not ok:
            await denied(self, update, "radars", snap)
            return ConversationHandler.END
        try:
            return await old_radar_limit(self, update, context)
        except Exception:
            await asyncio.to_thread(self.db.refund_usage, user.id, "radars", 1)
            raise

    @wraps(old_radar_run)
    async def radar_run(self, update, context):
        user = update.effective_user
        if user:
            snap = await asyncio.to_thread(self.db.get_usage_snapshot, user.id)
            if (
                not snap.get("unlimited")
                and snap.get("active")
                and snap["totals"].get("radars", 0) <= 0
            ):
                await denied(self, update, "radars", snap)
                return ConversationHandler.END
        return await old_radar_run(self, update, context)

    @wraps(old_run_radar)
    async def run_radar(
        self, update, *, radar_id, niches, regions, result_limit, saved_now
    ):
        user = update.effective_user
        if not user or not (await available(self, update, "leads"))[0]:
            return ConversationHandler.END
        ok, snap = await asyncio.to_thread(self.db.consume_usage, user.id, "searches", 1)
        if not ok:
            await denied(self, update, "searches", snap)
            return ConversationHandler.END
        return await old_run_radar(
            self,
            update,
            radar_id=radar_id,
            niches=niches,
            regions=regions,
            result_limit=result_limit,
            saved_now=saved_now,
        )

    async def show_limits(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        del context
        self.ensure_account(update)
        user, message = update.effective_user, update.effective_message
        if not user or not message:
            return
        snap = await asyncio.to_thread(self.db.get_usage_snapshot, user.id)
        if snap.get("unlimited"):
            role = str(snap.get("role") or "user")
            period = "бессрочно"
            if role == "beta_tester":
                end = await asyncio.to_thread(self.db.get_beta_expires_at, user.id)
                period = f"до {end.strftime('%d.%m.%Y в %H:%M UTC')}" if end else "7 дней"
            await message.reply_text(
                "📊 Ваши лимиты\n\n"
                f"Роль: {ROLE_LABELS.get(role, role)}\n"
                "Поиски: без ограничений\nЛиды: без ограничений\n"
                "Анализы: без ограничений\nСообщения: без ограничений\n"
                f"Радары: без ограничений\nСрок доступа: {period}",
                reply_markup=MENU,
            )
            return
        if not snap.get("active"):
            await message.reply_text(
                "📊 Ваши лимиты\n\nАктивного тарифа нет.\n"
                "Откройте «⭐ Тарифы», чтобы продолжить работу.",
                reply_markup=MENU,
            )
            return
        access, totals, left = snap["access"], snap["totals"], snap["remaining"]
        await message.reply_text(
            "📊 Ваши лимиты\n\n"
            f"Тариф: {access['plan_name']}\n"
            f"Действует до: {access['ends_at'].strftime('%d.%m.%Y')}\n\n"
            "Осталось:\n"
            f"Поиски: {left['searches']} из {totals['searches']}\n"
            f"Лиды: {left['leads']} из {totals['leads']}\n"
            f"Анализы: {left['analyses']} из {totals['analyses']}\n"
            f"Сообщения: {left['messages']} из {totals['messages']}\n"
            f"Радары: {left['radars']} из {totals['radars']}",
            reply_markup=MENU,
        )

    bot_class.find_start = find_start
    bot_class._search_and_reply = search
    bot_class.message_start = message_start
    bot_class.receive_lead_id = message
    bot_class.analyze_start = analyze_start
    bot_class.receive_analyze_lead_id = analyze
    bot_class.radar_start = radar_start
    bot_class.receive_radar_limit = radar_limit
    bot_class.radar_run = radar_run
    bot_class._run_radar = run_radar
    bot_class.show_limits = show_limits
    bot_class._usage_limits_installed = True
