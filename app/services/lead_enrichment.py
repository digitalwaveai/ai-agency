from __future__ import annotations

import re
from urllib.parse import urlparse

from app.schemas import LeadCreate, SearchRequest
from app.services.pain_detection import detect_pain
from app.services.search_quality import (
    BOOKING_HOSTS,
    PLACEHOLDER_HOSTS,
    SOCIAL_HOSTS,
    is_host_in,
)
from app.services.search_service import SearchResult


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _clean_name(title: str) -> str:
    value = re.split(r"\s+[|—]\s+", title, maxsplit=1)[0].strip()
    value = re.sub(r"\s+", " ", value)
    return value[:255] or "не найден"


def result_to_lead(result: SearchResult, req: SearchRequest) -> LeadCreate:
    text = f"{result.title} {result.snippet}".strip()
    email_match = EMAIL_RE.search(text)
    phone_match = PHONE_RE.search(text)
    email = email_match.group(0) if email_match else None
    phone = phone_match.group(0) if phone_match else None
    lower = text.lower()
    host = _host(result.url)

    is_social = is_host_in(host, SOCIAL_HOSTS)
    is_booking = is_host_in(host, BOOKING_HOSTS)
    is_placeholder = is_host_in(host, PLACEHOLDER_HOSTS)
    website_url = None if is_social or is_booking or is_placeholder else result.url

    return LeadCreate(
        name=_clean_name(result.title),
        niche=req.niche,
        city=req.city,
        country=req.country,
        website_url=website_url,
        instagram_url=result.url if host.endswith("instagram.com") else None,
        telegram_url=result.url if host in {"t.me", "telegram.me"} else None,
        vk_url=result.url if host.endswith("vk.com") else None,
        tiktok_url=result.url if host.endswith("tiktok.com") else None,
        youtube_url=result.url if host.endswith("youtube.com") or host == "youtu.be" else None,
        email=email,
        phone=phone,
        whatsapp=phone if phone and any(word in lower for word in ("whatsapp", "ватсап")) else None,
        description=result.snippet,
        pain_points=detect_pain(text, req.target_pain),
        suggested_offer=", ".join(req.services) or None,
        source_url=result.url,
        source_type=result.source_type,
    )
