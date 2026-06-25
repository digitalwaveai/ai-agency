from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

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

AUDIT_VERSION = "2.0"

PLATFORM_SUFFIX_RE = re.compile(
    r"\s*(?:\||—|-)\s*(?:TikTok|Instagram|VK|ВКонтакте|"
    r"Telegram|Taplink|Facebook|YouTube)\s*$",
    re.IGNORECASE,
)

HANDLE_RE = re.compile(r"@[A-Za-z0-9_.]{3,}")

SURNAME_ENDINGS = (
    "ова",
    "ева",
    "ёва",
    "ина",
    "ына",
    "ская",
    "цкая",
    "ая",
    "ов",
    "ев",
    "ин",
    "ский",
    "цкий",
)

PAIN_KEYWORDS = {
    "booking": (
        "для записи",
        "запись",
        "пишите",
        "директ",
        "личные сообщения",
        "whatsapp",
        "ватсап",
        "telegram",
        "телеграм",
        "комментар",
    ),
    "price": (
        "прайс",
        "цены",
        "стоимость",
        "цена",
    ),
    "site": (
        "сайт",
        "страница",
        "лендинг",
    ),
}


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
    raw = AUDIT_VERSION + "\n" + "\n".join(str(value or "") for value in values)
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


def _normalize_spaces(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _source_handle(lead: Lead) -> str | None:
    for url in (
        lead.instagram_url,
        lead.tiktok_url,
        lead.telegram_url,
        lead.vk_url,
        lead.youtube_url,
        lead.source_url,
    ):
        if not url:
            continue

        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]

        if not parts:
            continue

        handle = parts[0]
        if handle.startswith("@"):
            return handle

        if parsed.netloc.lower() in {
            "instagram.com",
            "www.instagram.com",
            "tiktok.com",
            "www.tiktok.com",
            "t.me",
            "telegram.me",
        }:
            return f"@{handle}"

    return None


def _display_name(lead: Lead) -> str:
    raw = _normalize_spaces(lead.name)
    if not raw:
        return _source_handle(lead) or "Без названия"

    cleaned = PLATFORM_SUFFIX_RE.sub("", raw).strip(" |—-")

    segments = [
        segment.strip(" |—-")
        for segment in re.split(r"\s*\|\s*", cleaned)
        if segment.strip(" |—-")
    ]

    ignored = {
        str(lead.niche or "").strip().lower(),
        str(lead.city or "").strip().lower(),
        str(lead.country or "").strip().lower(),
        "tiktok",
        "instagram",
        "telegram",
        "taplink",
        "vk",
        "вконтакте",
        "facebook",
        "youtube",
    }

    meaningful = [
        segment
        for segment in segments
        if segment.lower() not in ignored
    ]
    candidate = meaningful[0] if meaningful else cleaned

    person_match = PERSON_RE.search(candidate)
    if person_match:
        return f"{person_match.group(1)} {person_match.group(2)}"

    handle_match = HANDLE_RE.search(candidate)
    if handle_match:
        return handle_match.group(0)

    source_handle = _source_handle(lead)
    generic_candidate = GENERIC_NAME_RE.search(candidate) is not None
    upper_generic = candidate.upper().startswith(
        (
            "ТВОЙ КОСМЕТОЛОГ",
            "ПРОФИЛЬ",
            "КОСМЕТОЛОГ МОСКВА",
        )
    )

    if source_handle and (generic_candidate or upper_generic):
        return source_handle

    candidate = re.sub(
        r"^(?:профиль|страница)\s+",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip()

    candidate = re.sub(
        r"\s+(?:косметолог|Москва|СПб|Санкт-Петербург)\s*$",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip(" |—-")

    if candidate and len(candidate) >= 3:
        return candidate[:120]

    return source_handle or "Без названия"


def _given_name(display_name: str) -> str | None:
    if display_name.startswith("@"):
        return None

    match = PERSON_RE.fullmatch(display_name)
    if not match:
        return None

    first, second = match.groups()

    if first.lower().endswith(SURNAME_ENDINGS):
        return second

    return first


def _pain_keyword_group(pain: str | None) -> tuple[str, ...]:
    lower = str(pain or "").lower()

    if any(word in lower for word in ("запис", "сообщ", "директ", "whatsapp", "ватсап")):
        return PAIN_KEYWORDS["booking"]
    if any(word in lower for word in ("прайс", "цен", "стоим")):
        return PAIN_KEYWORDS["price"]
    if "сайт" in lower:
        return PAIN_KEYWORDS["site"]

    return tuple(
        dict.fromkeys(
            keyword
            for values in PAIN_KEYWORDS.values()
            for keyword in values
        )
    )


def _trim_at_word_boundary(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text

    shortened = text[: limit + 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{shortened}…"


def _compact_evidence(
    evidence: str | None,
    *,
    pain: str | None,
    lead: Lead,
) -> str | None:
    text = _normalize_spaces(evidence)

    if not text:
        return None

    display_name = _display_name(lead)
    raw_name = _normalize_spaces(lead.name)

    for removable in (raw_name, display_name):
        if removable and len(removable) >= 3:
            text = re.sub(
                re.escape(removable),
                " ",
                text,
                flags=re.IGNORECASE,
            )

    text = re.sub(
        r"\b(?:TikTok|Instagram|Telegram|Taplink|VK|ВКонтакте|"
        r"Facebook|YouTube)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b\d+(?:[.,]\d+)?[KМM]?\s*(?:лайк\w*|подпис\w*|просмотр\w*)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = _normalize_spaces(text).strip(" |—-.,;:")

    keywords = _pain_keyword_group(pain)
    sentences = [
        part.strip(" |—-.,;:")
        for part in re.split(r"(?<=[.!?])\s+|\s*[|•]\s*|\n+", text)
        if part.strip(" |—-.,;:")
    ]

    ranked: list[tuple[int, int, str]] = []
    for sentence in sentences:
        lower = sentence.lower()
        hits = sum(1 for keyword in keywords if keyword in lower)
        if hits:
            ranked.append((hits, -len(sentence), sentence))

    if ranked:
        ranked.sort(reverse=True)
        best = ranked[0][2]
    else:
        booking_match = re.search(
            r"(?i)(?:для\s+записи|запись(?:\s+через|\s+в|\s+по)?|"
            r"пишите\s+(?:в|через))[^.!?|]{0,170}",
            text,
        )
        best = booking_match.group(0).strip() if booking_match else text

    best = re.sub(
        r"^(?:Москва|косметолог|частный косметолог)\s*[,—:-]*\s*",
        "",
        best,
        flags=re.IGNORECASE,
    )
    best = _normalize_spaces(best).strip(" «»|—-.,;:")

    if not best:
        return None

    return _trim_at_word_boundary(best, 180)


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


def _message_opening(
    *,
    display_name: str,
    classification: str,
) -> tuple[str, str]:
    given_name = _given_name(display_name)

    if given_name:
        return f"Здравствуйте, {given_name}!", ""

    if display_name.startswith("@"):
        return "Здравствуйте!", f"Увидел профиль {display_name}. "

    if classification == "малый бизнес":
        return f"Здравствуйте, команда {display_name}!", ""

    if display_name and display_name != "Без названия":
        return f"Здравствуйте, {display_name}!", ""

    return "Здравствуйте!", ""


def _first_message(
    lead: Lead,
    *,
    display_name: str,
    classification: str,
    pain: str | None,
    evidence: str | None,
    offers: list[str],
) -> str:
    greeting, profile_reference = _message_opening(
        display_name=display_name,
        classification=classification,
    )
    offer = offers[0] if offers else "автоматизацию записи"

    if evidence:
        observation = f'Обратил внимание: «{evidence}». '
    elif pain:
        observation = (
            f"Вижу возможную точку роста: {pain.lower().rstrip('.')}. "
        )
    else:
        observation = (
            "Посмотрел формат записи и коммуникации с клиентами. "
        )

    return (
        f"{greeting} {profile_reference}{observation}"
        f"Можно упростить этот процесс через {offer.lower()}, "
        "чтобы клиентам было легче записываться, а вам — тратить меньше "
        "времени на ручную обработку сообщений. Могу показать короткий "
        "пример решения именно под ваш формат — интересно?"
    )


def build_lead_audit(lead: Lead) -> dict[str, Any]:
    pain, raw_evidence = _pain_parts(lead.pain_points)
    classification = _identity_classification(lead)
    display_name = _display_name(lead)
    evidence = _compact_evidence(
        raw_evidence,
        pain=pain,
        lead=lead,
    )
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
        "display_name": display_name,
        "classification": classification,
        "confidence": confidence,
        "fit_level": _fit_level(confidence, warnings),
        "why_fit": why_fit,
        "pain": pain,
        "evidence": evidence,
        "existing_assets": existing_assets,
        "do_not_offer": do_not_offer,
        "best_offer": best_offer,
        "first_message": _first_message(
            lead,
            display_name=display_name,
            classification=classification,
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
