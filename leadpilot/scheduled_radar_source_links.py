from __future__ import annotations

from functools import wraps
from typing import Any

from telegram.ext import ExtBot

from .compact_source_links import _compact_plain_source_text

_PATCHED = False


def _compact_radar_text(value: object) -> tuple[str, bool]:
    text = str(value or "")
    if not text.startswith("📡 Радар ID "):
        return text, False
    return _compact_plain_source_text(text)


def install_scheduled_radar_source_links() -> None:
    """Compact raw source URLs only in scheduled-radar lead messages."""
    global _PATCHED
    if _PATCHED:
        return

    original_send_message = ExtBot.send_message

    @wraps(original_send_message)
    async def send_message(self: ExtBot, *args: Any, **kwargs: Any):
        if kwargs.get("parse_mode") is None and not kwargs.get("entities"):
            original_text = kwargs.get("text")
            text_arg_index: int | None = None
            if original_text is None and len(args) >= 2:
                original_text = args[1]
                text_arg_index = 1

            compact_text, changed = _compact_radar_text(original_text)
            if changed:
                if "text" in kwargs:
                    kwargs["text"] = compact_text
                elif text_arg_index is not None:
                    mutable_args = list(args)
                    mutable_args[text_arg_index] = compact_text
                    args = tuple(mutable_args)

                kwargs["parse_mode"] = "HTML"
                if "link_preview_options" not in kwargs:
                    kwargs.setdefault("disable_web_page_preview", True)

        return await original_send_message(self, *args, **kwargs)

    ExtBot.send_message = send_message
    _PATCHED = True
