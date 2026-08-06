from __future__ import annotations

import html
import re
from functools import wraps
from typing import Any
from urllib.parse import urlparse

from telegram import Message, ReplyKeyboardRemove, Update

from . import bot as bot_module
from . import owner_emergency_actions as owner_actions
from .models import Lead


SOURCE_LINE_RE = re.compile(
    r"^\s*Источник:\s*(https?://\S+)\s*$",
    re.IGNORECASE,
)
_REPLY_TEXT_PATCHED = False


def _clip(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 1)].rstrip() + "…"


def _source_label(url: str) -> str:
    raw = str(url or "").strip()
    lowered = raw.lower()
    try:
        host = urlparse(raw).netloc.lower().removeprefix("www.")
    except ValueError:
        host = ""

    if "google.com/maps" in lowered or "google.ru/maps" in lowered:
        return "Google Карты"
    if host == "vk.com" or host.endswith(".vk.com"):
        return "ВКонтакте"
    if host in {"t.me", "telegram.me"}:
        return "Telegram"
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return "Instagram"
    if host:
        return host
    return "Открыть источник"


def _source_link(url: object) -> str:
    raw = str(url or "").strip()
    label = html.escape(_source_label(raw))
    try:
        parsed = urlparse(raw)
    except ValueError:
        parsed = None
    if not parsed or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return label
    escaped_url = html.escape(raw, quote=True)
    return f'<a href="{escaped_url}">{label}</a>'


def _compact_plain_source_text(value: object) -> tuple[str, bool]:
    """Turn raw source URL lines into compact HTML links.

    This is intentionally applied only to messages that do not already use a
    parse mode. Every other line is escaped before HTML mode is enabled, so
    company names and addresses containing &, < or > remain safe.
    """
    text = str(value or "")
    changed = False
    rendered: list[str] = []

    for line in text.splitlines():
        match = SOURCE_LINE_RE.fullmatch(line)
        if match:
            rendered.append(f"Источник: {_source_link(match.group(1))}")
            changed = True
        else:
            rendered.append(html.escape(line, quote=False))

    if not changed:
        return text, False
    return "\n".join(rendered), True


def _install_global_reply_text_patch() -> None:
    """Cover every current and future plain-text lead output path."""
    global _REPLY_TEXT_PATCHED
    if _REPLY_TEXT_PATCHED:
        return

    original_reply_text = Message.reply_text

    @wraps(original_reply_text)
    async def reply_text(self: Message, *args: Any, **kwargs: Any):
        # Existing HTML/Markdown messages already control their own rendering.
        if kwargs.get("parse_mode") is None and not kwargs.get("entities"):
            if args:
                original_text = args[0]
            else:
                original_text = kwargs.get("text", "")

            compact_text, changed = _compact_plain_source_text(original_text)
            if changed:
                if args:
                    args = (compact_text, *args[1:])
                else:
                    kwargs["text"] = compact_text
                kwargs["parse_mode"] = "HTML"
                if "link_preview_options" not in kwargs:
                    kwargs.setdefault("disable_web_page_preview", True)

        return await original_reply_text(self, *args, **kwargs)

    Message.reply_text = reply_text
    _REPLY_TEXT_PATCHED = True


def _format_lead_html(lead: Lead) -> str:
    lines = [
        f"ID {lead.id} · {html.escape(_clip(lead.name, 220))}",
        f"Релевантность: {int(lead.score)}/100",
        f"Статус: {html.escape(_clip(lead.status, 80))}",
        f"Контакт: {html.escape(_clip(lead.contact, 500))}",
    ]
    if lead.address:
        lines.append(f"Адрес: {html.escape(_clip(lead.address, 500))}")
    if lead.source_url and not str(lead.source_url).startswith(
        ("demo://", "serpapi://")
    ):
        lines.append(f"Источник: {_source_link(lead.source_url)}")
    return "\n".join(lines)


def _lead_blocks(bot: Any, leads: list[Lead]) -> list[str]:
    del bot
    return [_format_lead_html(lead) for lead in leads]


async def _send_leads(
    bot: Any,
    update: Update,
    leads: list[Lead],
    *,
    suffix: str = "",
    remove_keyboard: bool = False,
) -> None:
    del bot
    message = update.effective_message
    if message is None:
        return

    chunks: list[str] = []
    current = ""
    for block in _lead_blocks(None, leads):
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) <= 3300:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = block
    if current:
        chunks.append(current)

    escaped_suffix = html.escape(suffix)
    if escaped_suffix:
        if chunks and len(chunks[-1]) + len(escaped_suffix) + 2 <= 3900:
            chunks[-1] += f"\n\n{escaped_suffix}"
        else:
            chunks.append(escaped_suffix)

    reply_markup = ReplyKeyboardRemove() if remove_keyboard else bot_module.MENU
    for index, chunk in enumerate(chunks):
        await message.reply_text(
            chunk,
            parse_mode="HTML",
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
        f"Компания: {html.escape(_clip(lead.name, 300))}\n"
        f"Рейтинг: {int(lead.score)}/100\n"
        f"Контакт: {html.escape(_clip(lead.contact, 500))}\n"
        f"Адрес: {html.escape(_clip(lead.address or 'не найден', 500))}\n"
        f"Описание: {html.escape(_clip(lead.snippet or 'нет данных', 1000))}\n\n"
        f"Сильные сигналы: {html.escape(', '.join(strengths) or 'не обнаружены')}\n"
        f"Что проверить: {html.escape(', '.join(gaps) or 'критичных пробелов нет')}\n\n"
        "Следующий шаг: проверьте источник и подготовьте персональное "
        "сообщение без массовой рассылки."
    )
    if lead.source_url and not str(lead.source_url).startswith(
        ("demo://", "serpapi://")
    ):
        text += f"\nИсточник: {_source_link(lead.source_url)}"
    return text


def install_compact_source_links() -> None:
    """Use compact clickable source labels in every lead output path."""
    owner_actions._lead_blocks = _lead_blocks
    owner_actions._send_leads = _send_leads
    owner_actions._analysis_text = _analysis_text
    _install_global_reply_text_patch()
