from __future__ import annotations

import html
from typing import Any
from urllib.parse import urlparse

from telegram import ReplyKeyboardRemove, Update

from . import bot as bot_module
from . import owner_emergency_actions as owner_actions
from .models import Lead


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
        f"Компания: {_clip(lead.name, 300)}\n"
        f"Рейтинг: {int(lead.score)}/100\n"
        f"Контакт: {_clip(lead.contact, 500)}\n"
        f"Адрес: {_clip(lead.address or 'не найден', 500)}\n"
        f"Описание: {_clip(lead.snippet or 'нет данных', 1000)}\n\n"
        f"Сильные сигналы: {', '.join(strengths) or 'не обнаружены'}\n"
        f"Что проверить: {', '.join(gaps) or 'критичных пробелов нет'}\n\n"
        "Следующий шаг: проверьте источник и подготовьте персональное "
        "сообщение без массовой рассылки."
    )
    if lead.source_url and not str(lead.source_url).startswith(
        ("demo://", "serpapi://")
    ):
        text += f"\nИсточник: {_source_label(str(lead.source_url))}"
    return text


def install_compact_source_links() -> None:
    """Replace only the owner lead output with compact clickable source labels."""
    owner_actions._lead_blocks = _lead_blocks
    owner_actions._send_leads = _send_leads
    owner_actions._analysis_text = _analysis_text
