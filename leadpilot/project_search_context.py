from __future__ import annotations

import re
from functools import wraps
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from .bot import MENU

TARGET_SEPARATOR = " ||| "
EXCLUSION_SEPARATOR = " ||- "

WORD_RE = re.compile(r"[a-zа-яё0-9]+(?:-[a-zа-яё0-9]+)?", re.IGNORECASE)
STOP_WORDS = {
    "и", "или", "а", "но", "в", "во", "на", "по", "с", "со", "к", "ко",
    "у", "из", "для", "от", "до", "под", "над", "при", "через", "без",
    "про", "об", "это", "этот", "эта", "эти", "как", "что", "чтобы",
    "который", "которые", "которым", "которых", "где", "когда", "нет",
    "никаких", "the", "a", "an", "and", "or", "for", "to", "of", "in",
    "on", "with",
}
GENERIC_ROOTS = {
    "друг", "проект", "компан", "клиент", "орган", "крупн", "сильн",
    "собств", "готов", "действ", "актив", "любые", "прочие", "разные",
    "business", "company", "client", "project",
}


def _normalize(value: object) -> str:
    text = str(value or "").lower().replace("ё", "е")
    return re.sub(r"\s+", " ", text).strip()


def _root(word: str) -> str:
    value = _normalize(word).strip("-")
    if len(value) >= 9:
        return value[:7]
    if len(value) >= 6:
        return value[:5]
    return value


def _words(value: object) -> list[str]:
    return [_normalize(item) for item in WORD_RE.findall(_normalize(value))]


def _meaningful_roots(value: str) -> set[str]:
    roots: set[str] = set()
    for word in _words(value):
        if word in STOP_WORDS:
            continue
        root = _root(word)
        if root in GENERIC_ROOTS:
            continue
        if len(root) >= 3 or root in {"ai", "ии", "wb", "вб"}:
            roots.add(root)
    return roots


def _exclusion_groups(value: str) -> list[set[str]]:
    chunks = re.split(r"[,;\n]+|\s+(?:и|или)\s+", _normalize(value))
    groups = [_meaningful_roots(chunk) for chunk in chunks]
    return [group for group in groups if group]


def _lead_text(lead: Any) -> str:
    return _normalize(
        " ".join(
            str(value or "")
            for value in (
                getattr(lead, "name", ""),
                getattr(lead, "snippet", ""),
                getattr(lead, "address", ""),
                getattr(lead, "website", ""),
                getattr(lead, "source_url", ""),
            )
        )
    )


def _matches_exclusion(lead: Any, exclusions: str) -> bool:
    if not exclusions.strip():
        return False
    lead_roots = {_root(word) for word in _words(_lead_text(lead))}
    return any(group.issubset(lead_roots) for group in _exclusion_groups(exclusions))


def install_project_search_context(
    bot_class: type[Any], serpapi_class: type[Any]
) -> None:
    """Use project priorities and exclusions in every project-based search."""
    if getattr(bot_class, "_project_search_context_installed", False):
        return

    old_search = serpapi_class.search

    @wraps(old_search)
    def search(self: Any, target: str, region: str, limit: int = 5):
        requested_limit = max(1, min(int(limit), 20))
        search_target, separator, exclusions = target.partition(EXCLUSION_SEPARATOR)
        candidate_limit = min(max(requested_limit * 3, requested_limit), 20)
        leads = old_search(self, search_target, region, candidate_limit)
        if not separator or not exclusions.strip():
            return leads[:requested_limit]
        return [
            lead for lead in leads if not _matches_exclusion(lead, exclusions)
        ][:requested_limit]

    async def select_search_limit(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        query = update.callback_query
        if not query or not query.from_user:
            return ConversationHandler.END
        await query.answer()
        if query.data == "search_limit:cancel":
            context.user_data.clear()
            await query.message.reply_text("Поиск отменён.", reply_markup=MENU)
            return ConversationHandler.END
        try:
            limit = int((query.data or "").partition(":")[2])
        except ValueError:
            await query.message.reply_text(
                "Не удалось определить количество. Запустите поиск ещё раз.",
                reply_markup=MENU,
            )
            return ConversationHandler.END

        project = dict(context.user_data["search_project"])
        segment = str(context.user_data["search_segment"]).strip()
        region = str(context.user_data["search_region"]).strip()
        project_niche = str(project.get("niche") or "").strip()
        priorities = str(project.get("priorities") or "").strip()
        exclusions = str(project.get("exclusions") or "").strip()

        primary_parts = [segment]
        if priorities and _normalize(priorities) not in _normalize(segment):
            primary_parts.append(priorities)
        primary = " ".join(primary_parts)

        target = primary
        if (
            project_niche
            and _normalize(project_niche) not in _normalize(primary)
            and _normalize(primary) not in _normalize(project_niche)
        ):
            target = f"{primary}{TARGET_SEPARATOR}{project_niche}"
        if exclusions:
            target = f"{target}{EXCLUSION_SEPARATOR}{exclusions}"

        await query.message.reply_text(
            f"Ищу для проекта «{project['name']}»: {segment}, {region}. "
            "Учитываю приоритеты и исключения из анкеты…"
        )
        await self._search_and_reply(
            update,
            target,
            region,
            limit,
            project_id=int(project["id"]),
        )
        context.user_data.clear()
        return ConversationHandler.END

    serpapi_class.search = search
    bot_class.select_search_limit = select_search_limit
    bot_class._project_search_context_installed = True
