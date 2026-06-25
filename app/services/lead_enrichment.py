from __future__ import annotations

import re
from urllib.parse import urlparse

from app.schemas import LeadCreate, SearchRequest
from app.services.pain_detection import detect_pain
from app.services.search_quality import (
    BOOKING_HOSTS,
    PLACEHOLDER_HOSTS,
    SOCIAL_HOSTS,
    assess_identity,
    canonical_social_profile_url,
    extract_valid_phone,
    is_host_in,
)
from app.services.search_service import SearchResult


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def result_to_lead(result: SearchResult, req: SearchRequest) -> LeadCreate:
    text = f"{result.title} {result.snippet}".strip()
    email_match = EMAIL_RE.search(text)
    email = email_match.group(0) if email_match else None
    phone = extract_valid_phone(text)
    lower = text.lower()
    host = _host(result.url)

    identity = assess_identity(
        title=result.title,
        snippet=result.snippet,
        url=result.url,
        niche=req.niche,
        city=req.city,
    )

    profile_url = result.profile_url or canonical_social_profile_url(result.url, text)
    profile_host = _host(profile_url or "")

    is_social = is_host_in(host, SOCIAL_HOSTS)
    is_booking = is_host_in(host, BOOKING_HOSTS)
    is_placeholder = is_host_in(host, PLACEHOLDER_HOSTS)
    website_url = None if is_social or is_booking or is_placeholder else result.url
    source_url = profile_url if is_social and profile_url else result.url

    return LeadCreate(
        name=identity.name or result.title[:255] or "не найден",
        niche=req.niche,
        city=req.city,
        country=req.country,
        website_url=website_url,
        instagram_url=profile_url if profile_host.endswith("instagram.com") else None,
        telegram_url=profile_url if profile_host in {"t.me", "telegram.me"} else None,
        vk_url=profile_url if profile_host.endswith("vk.com") else None,
        tiktok_url=profile_url if profile_host.endswith("tiktok.com") else None,
        youtube_url=(
            profile_url
            if profile_host.endswith("youtube.com") or profile_host == "youtu.be"
            else None
        ),
        email=email,
        phone=phone,
        whatsapp=(
            phone
            if phone and any(word in lower for word in ("whatsapp", "ватсап"))
            else None
        ),
        description=result.snippet,
        pain_points=detect_pain(text, req.target_pain),
        suggested_offer=", ".join(req.services) or None,
        source_url=source_url,
        source_type=result.source_type,
        score_reason=(
            f"предварительная оценка поиска: {result.quality_reason}"
            if result.quality_reason
            else None
        ),
    )
