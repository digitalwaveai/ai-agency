from __future__ import annotations

import re

from app.schemas import LeadCreate, SearchRequest
from app.services.search_quality import (
    CHAIN_RE,
    DIRECTORY_TEXT_RE,
    JOB_TEXT_RE,
    pain_is_confirmed,
    offer_is_relevant,
    text_matches_city,
    text_matches_niche,
)


PRIVATE_BUSINESS_RE = re.compile(
    r"\b(?:частн\w*|мастер|специалист|кабинет|студия|принимаю|веду\s+при[её]м)\b",
    re.IGNORECASE,
)

MANUAL_BOOKING_RE = re.compile(
    r"(?:личн\w*\s+сообщ\w*|директ|direct|whatsapp|ватсап|telegram|телеграм|"
    r"для\s+записи\s+пишите|запись\s+через)",
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
        )
    ).lower()

    niche = req.niche if req else lead.niche or ""
    city = req.city if req else lead.city or ""
    score = 0
    reasons: list[str] = []

    if CHAIN_RE.search(evidence_text):
        return 0, "жесткий отказ: сеть, франшиза или филиалы"

    if DIRECTORY_TEXT_RE.search(evidence_text) or JOB_TEXT_RE.search(evidence_text):
        return 0, "жесткий отказ: каталог, рейтинг, отзывы или вакансия"

    if niche and text_matches_niche(evidence_text, niche):
        score += 30
        reasons.append("подтверждена ниша +30")
    else:
        score -= 35
        reasons.append("ниша не подтверждена -35")

    if city and text_matches_city(evidence_text, city):
        score += 15
        reasons.append("подтвержден город +15")
    else:
        score -= 15
        reasons.append("город не подтвержден -15")

    if PRIVATE_BUSINESS_RE.search(evidence_text):
        score += 20
        reasons.append("частный специалист или небольшой бизнес +20")

    has_contact = any(
        [
            lead.email,
            lead.phone,
            lead.telegram_url,
            lead.instagram_url,
            lead.vk_url,
            lead.whatsapp,
        ]
    )

    if has_contact:
        score += 15
        reasons.append("есть прямой контакт +15")
    else:
        score -= 15
        reasons.append("прямой контакт не найден -15")

    if pain_is_confirmed(lead.pain_points):
        score += 15
        reasons.append("подтверждена проблема бизнеса +15")

        if req and req.target_pain:
            score += 10
            reasons.append("проблема совпадает с целевой болью +10")
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

    return max(0, min(100, score)), "; ".join(reasons)
