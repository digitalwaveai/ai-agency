from __future__ import annotations

import re

from app.schemas import LeadCreate, SearchRequest
from app.services.search_quality import (
    assess_identity,
    enterprise_rejection_reason,
    hard_rejection_reason,
    offer_is_relevant,
    pain_is_confirmed,
    pain_is_explicit,
    text_matches_city,
    text_matches_niche,
)


PRIVATE_BUSINESS_RE = re.compile(
    r"\b(?:частн\w*|мастер|специалист|кабинет|студия|принимаю|веду\s+при[её]м)\b",
    re.IGNORECASE,
)

MANUAL_BOOKING_RE = re.compile(
    r"(?:личн\w*\s+сообщ\w*|директ|direct|whatsapp|ватсап|telegram|телеграм|"
    r"для\s+записи\s+пишите|запись\s+через|пишите\s+в\s+комментари)",
    re.IGNORECASE,
)


def score_lead(lead: LeadCreate, req: SearchRequest | None = None) -> tuple[int, str]:
    evidence_text = " ".join(
        str(value or "")
        for value in (
            lead.name,
            lead.description,
            lead.pain_points,
            lead.source_url,
            lead.website_url,
        )
    )

    niche = req.niche if req else lead.niche or ""
    city = req.city if req else lead.city or ""
    exclude = req.exclude if req else ""
    score = 0
    reasons: list[str] = []

    rejection = hard_rejection_reason(evidence_text, lead.source_url, exclude)
    if rejection:
        return 0, f"жесткий отказ: {rejection}"

    enterprise = enterprise_rejection_reason(evidence_text)
    if enterprise:
        return 0, f"жесткий отказ: {enterprise}"

    identity = assess_identity(
        title=lead.name,
        snippet=lead.description or "",
        url=lead.source_url,
        niche=niche,
        city=city,
    )
    if not identity.accepted:
        return 0, f"жесткий отказ: {identity.reason}"

    niche_match = bool(niche and text_matches_niche(evidence_text, niche))
    city_match = bool(city and text_matches_city(evidence_text, city))

    if niche_match:
        score += 25
        reasons.append("подтверждена ниша +25")
    else:
        return 0, "жесткий отказ: ниша не подтверждена"

    score += identity.score
    reasons.append(f"{identity.reason} +{identity.score}")

    if city_match:
        score += 15
        reasons.append("подтвержден город +15")
    else:
        score -= 15
        reasons.append("город не подтвержден -15")

    if PRIVATE_BUSINESS_RE.search(evidence_text):
        score += 15
        reasons.append("частный специалист или небольшой бизнес +15")

    has_direct_contact = any([lead.email, lead.phone, lead.whatsapp])
    has_social_profile = any(
        [
            lead.telegram_url,
            lead.instagram_url,
            lead.vk_url,
            lead.tiktok_url,
            lead.youtube_url,
        ]
    )

    if has_direct_contact:
        score += 15
        reasons.append("есть прямой контакт: телефон, email или WhatsApp +15")
    elif has_social_profile:
        score += 5
        reasons.append("есть только социальный профиль +5")
    else:
        score -= 15
        reasons.append("контакт не найден -15")

    explicit_pain = pain_is_explicit(lead.pain_points)
    confirmed_pain = pain_is_confirmed(lead.pain_points)

    if explicit_pain:
        score += 20
        reasons.append("боль подтверждена явным текстом +20")
    elif confirmed_pain:
        score += 5
        reasons.append("боль предположена по отсутствию функции +5")
    elif req and req.target_pain:
        score -= 25
        reasons.append("целевая боль не подтверждена -25")

    if req and req.services:
        if offer_is_relevant(lead.suggested_offer):
            score += 10
            reasons.append("есть релевантный оффер +10")
        else:
            score -= 20
            reasons.append("нет подтвержденной потребности в услуге -20")

    if MANUAL_BOOKING_RE.search(evidence_text):
        score += 10
        reasons.append("есть признаки ручной записи +10")

    caps: list[tuple[int, str]] = []

    if identity.generic:
        caps.append((60, "нет подтвержденного имени бизнеса"))

    if not city_match:
        caps.append((55, "не подтвержден город"))

    if not has_direct_contact:
        caps.append((75, "нет прямого контакта"))

    if req and req.target_pain and not explicit_pain:
        caps.append((70, "нет явного подтверждения целевой боли"))

    for maximum, reason in caps:
        if score > maximum:
            score = maximum
            reasons.append(f"потолок {maximum}: {reason}")

    return max(0, min(100, score)), "; ".join(reasons)
