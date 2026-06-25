from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Lead, LeadAudit
from app.services.search_quality import is_valid_phone, pain_is_confirmed


BOOKING_RE = re.compile(
    r"(?:онлайн[-\s]?запис\w*|записаться\s+онлайн|"
    r"yclients|dikidi|altegio|appointer|easyweek|"
    r"личн\w*\s+кабинет|мобильн\w*\s+приложени\w*)",
    re.IGNORECASE,
)

PERSON_RE = re.compile(
    r"(?:^|\s)([А-ЯЁ][а-яё]{2,})\s+([А-ЯЁ][а-яё]{2,})(?:\s|$)"
)

GENERIC_NAME_RE = re.compile(
    r"^(?:профиль|косметолог|частный косметолог|"
    r"косметологический кабинет|услуги|контакты|запись)\b",
    re.IGNORECASE,
)


def _lead_fingerprint(lead: Lead) -> str:
    values = (
        lead.name,
        lead.niche,
        lead.city,
        lead.country,
        lead.website_url,
        lead.instagram_url,
        lead.tiktok_url,
        lead.telegram_url,
        lead.vk_url,
        lead.youtube_url,
        lead.email,
        lead.phone,
        lead.whatsapp,
        lead.description,
        lead.pain_points,
        lead.suggested_offer,
        lead.source_url,
        lead.source_type,
        lead.score,
        lead.score_reason,
        lead.status,
        lead.notes,
        lead.last_updated_at.isoformat() if lead.last_updated_at else "",
    )
    raw = "\n".join(str(value or "") for value in values)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _pain_parts(pain_points: str | None) -> tuple[str | None, str | None]:
    text = str(pain_points or "").strip()

    if not text or text.lower() in {
        "не найден",
        "выбранная боль не подтверждена",
    }:
        return None, None

    pain, separator, evidence = text.partition("\nПодтверждение:")

    if not separator:
        pain, separator, evidence = text.partition("Подтверждение:")

    clean_pain = pain.strip(" \n:«»") or None
    clean_evidence = evidence.strip(" \n:«»") or None if separator else None
    return clean_pain, clean_evidence


def _identity_classification(lead: Lead) -> str:
    name = str(lead.name or "").strip()
    lower = " ".join(
        str(value or "").lower()
        for value in (
            lead.name,
            lead.description,
            lead.source_url,
        )
    )

    if PERSON_RE.search(name):
        return "частный специалист"

    if any(
        marker in lower
        for marker in (
            "частный косметолог",
            "врач-косметолог",
            "мастер косметолог",
            "принимаю",
            "веду приём",
        )
    ):
        return "частный специалист"

    if any(
        marker in lower
        for marker in (
            "кабинет",
            "студия",
            "clinic",
            "клиника",
            "центр",
            "салон",
        )
    ):
        return "малый бизнес"

    if name.startswith("@") or "@" in name or any(
        [
            lead.instagram_url,
            lead.tiktok_url,
            lead.telegram_url,
            lead.vk_url,
        ]
    ):
        return "профиль специалиста"

    return "требует ручной проверки"


def _existing_assets(lead: Lead) -> list[str]:
    assets: list[str] = []

    mapping = (
        ("Сайт", lead.website_url),
        ("Instagram", lead.instagram_url),
        ("TikTok", lead.tiktok_url),
        ("Telegram", lead.telegram_url),
        ("VK", lead.vk_url),
        ("YouTube", lead.youtube_url),
        ("Email", lead.email),
        ("Телефон", lead.phone if is_valid_phone(lead.phone) else None),
        ("WhatsApp", lead.whatsapp if is_valid_phone(lead.whatsapp) else None),
    )

    for label, value in mapping:
        if value and value != "не найден":
            assets.append(label)

    evidence_text = " ".join(
        str(value or "")
        for value in (
            lead.description,
            lead.pain_points,
            lead.score_reason,
            lead.website_url,
        )
    )

    if BOOKING_RE.search(evidence_text):
        assets.append("Онлайн-запись")

    return list(dict.fromkeys(assets))


def _do_not_offer(existing_assets: list[str]) -> list[str]:
    result: list[str] = []

    if "Сайт" in existing_assets:
        result.append("Создание базового сайта с нуля")
    if "Instagram" in existing_assets:
        result.append("Создание Instagram-профиля")
    if "Telegram" in existing_assets:
        result.append("Создание обычного Telegram-канала")
    if "Онлайн-запись" in existing_assets:
        result.append("Подключение базовой онлайн-записи")
    if "WhatsApp" in existing_assets:
        result.append("Подключение WhatsApp как единственного канала связи")

    return result


def _requested_offers(lead: Lead) -> list[str]:
    raw = str(lead.suggested_offer or "")
    parts = re.split(r"[,;\n]+", raw)
    return [part.strip() for part in parts if part.strip()]


def _best_offer(
    lead: Lead,
    *,
    existing_assets: list[str],
    pain: str | None,
) -> list[str]:
    candidates = _requested_offers(lead)

    pain_lower = str(pain or "").lower()

    if "запис" in pain_lower or "сообщен" in pain_lower:
        candidates.extend(
            [
                "Онлайн-запись с выбором услуги и времени",
                "Telegram-бот с напоминаниями и подтверждением записи",
            ]
        )

    if "прайс" in pain_lower or "цен" in pain_lower:
        candidates.append("Структурированный каталог услуг и цен")

    if "сайт" in pain_lower and "Сайт" not in existing_assets:
        candidates.append("Мини-сайт или лендинг с услугами и контактами")

    if not candidates:
        candidates.extend(
            [
                "Автоматизация обработки заявок",
                "Telegram-бот для записи и повторных касаний",
            ]
        )

    blocked_words: list[str] = []
    if "Сайт" in existing_assets:
        blocked_words.append("сайт")
    if "Онлайн-запись" in existing_assets:
        blocked_words.extend(["онлайн-запись", "онлайн запись"])
    if "Instagram" in existing_assets:
        blocked_words.append("instagram")

    filtered: list[str] = []
    for candidate in candidates:
        lower = candidate.lower()
        if any(word in lower for word in blocked_words):
            continue
        if candidate not in filtered:
            filtered.append(candidate)

    if not filtered:
        filtered.append("Telegram-бот для квалификации заявок и напоминаний")

    return filtered[:3]


def _why_fit(
    lead: Lead,
    *,
    classification: str,
    pain: str | None,
    evidence: str | None,
) -> list[str]:
    reasons: list[str] = []

    if classification in {
        "частный специалист",
        "профиль специалиста",
        "малый бизнес",
    }:
        reasons.append(f"Определён тип: {classification}")

    if lead.niche:
        reasons.append(f"Ниша подтверждена: {lead.niche}")

    if lead.city:
        reasons.append(f"Город подтверждён: {lead.city}")

    if is_valid_phone(lead.phone) or is_valid_phone(lead.whatsapp) or lead.email:
        reasons.append("Найден прямой контакт")

    if pain and evidence:
        reasons.append("Целевая боль подтверждена цитатой")

    if lead.score >= 70:
        reasons.append(f"Высокий поисковый score: {lead.score}")

    return reasons


def _warnings(
    lead: Lead,
    *,
    classification: str,
    evidence: str | None,
) -> list[str]:
    warnings: list[str] = []

    if classification == "требует ручной проверки":
        warnings.append("Личность или название бизнеса подтверждены не полностью")

    if not evidence:
        warnings.append("Целевая боль не подтверждена прямой цитатой")

    if not (
        is_valid_phone(lead.phone)
        or is_valid_phone(lead.whatsapp)
        or lead.email
    ):
        warnings.append("Не найден прямой телефон, WhatsApp или email")

    if GENERIC_NAME_RE.search(str(lead.name or "").strip()):
        warnings.append("Название выглядит общим — проверьте профиль вручную")

    if lead.source_type in {"facebook", "directory", "aggregator"}:
        warnings.append("Источник требует дополнительной проверки")

    return warnings


def _fit_level(confidence: int, warnings: list[str]) -> str:
    if confidence >= 80 and len(warnings) <= 1:
        return "горячий"
    if confidence >= 60:
        return "тёплый"
    return "требует проверки"


def _first_name(name: str) -> str:
    cleaned = re.sub(r"\([^)]*\)", "", name or "").strip()
    match = PERSON_RE.search(cleaned)

    if match:
        return match.group(1)

    token = cleaned.split()[0] if cleaned else "Здравствуйте"
    return token.strip("@|—-") or "Здравствуйте"


def _first_message(
    lead: Lead,
    *,
    pain: str | None,
    evidence: str | None,
    offers: list[str],
) -> str:
    greeting = _first_name(lead.name)
    offer = offers[0] if offers else "автоматизацию записи"

    if evidence:
        observation = (
            f'увидел, что сейчас у вас встречается формат: «{evidence}»'
        )
    elif pain:
        observation = f"обратил внимание на возможную точку роста: {pain.lower()}"
    else:
        observation = "посмотрел ваш профиль и формат записи клиентов"

    return (
        f"Здравствуйте, {greeting}! Я {observation}. "
        f"Можно упростить этот процесс через {offer.lower()}, "
        "чтобы клиенты быстрее записывались, а вам приходилось меньше "
        "обрабатывать сообщения вручную. Могу показать короткий пример "
        "решения именно под ваш формат — интересно?"
    )


def build_lead_audit(lead: Lead) -> dict[str, Any]:
    pain, evidence = _pain_parts(lead.pain_points)
    classification = _identity_classification(lead)
    existing_assets = _existing_assets(lead)
    do_not_offer = _do_not_offer(existing_assets)
    best_offer = _best_offer(
        lead,
        existing_assets=existing_assets,
        pain=pain,
    )
    warnings = _warnings(
        lead,
        classification=classification,
        evidence=evidence,
    )

    confidence = int(lead.score or 0)

    if not evidence:
        confidence = min(confidence, 70)
    if classification == "требует ручной проверки":
        confidence = min(confidence, 50)
    if not (
        is_valid_phone(lead.phone)
        or is_valid_phone(lead.whatsapp)
        or lead.email
    ):
        confidence = min(confidence, 65)

    confidence = max(0, min(100, confidence))

    why_fit = _why_fit(
        lead,
        classification=classification,
        pain=pain,
        evidence=evidence,
    )

    return {
        "lead_id": lead.id,
        "lead_name": lead.name,
        "classification": classification,
        "confidence": confidence,
        "fit_level": _fit_level(confidence, warnings),
        "why_fit": why_fit,
        "evidence": evidence,
        "existing_assets": existing_assets,
        "do_not_offer": do_not_offer,
        "best_offer": best_offer,
        "first_message": _first_message(
            lead,
            pain=pain,
            evidence=evidence,
            offers=best_offer,
        ),
        "warnings": warnings,
    }


def get_or_create_lead_audit(
    db: Session,
    lead: Lead,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    fingerprint = _lead_fingerprint(lead)
    record = (
        db.query(LeadAudit)
        .filter(LeadAudit.lead_id == lead.id)
        .one_or_none()
    )

    if (
        record
        and record.source_fingerprint == fingerprint
        and not force_refresh
    ):
        payload = json.loads(record.payload_json)
        payload["generated_at"] = record.generated_at
        payload["cached"] = True
        return payload

    payload = build_lead_audit(lead)
    now = datetime.utcnow()

    if record is None:
        record = LeadAudit(
            lead_id=lead.id,
            source_fingerprint=fingerprint,
            payload_json="{}",
            generated_at=now,
            updated_at=now,
        )
        db.add(record)
    else:
        record.source_fingerprint = fingerprint
        record.generated_at = now
        record.updated_at = now

    record.payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
    )

    db.commit()
    db.refresh(record)

    payload["generated_at"] = record.generated_at
    payload["cached"] = False
    return payload
