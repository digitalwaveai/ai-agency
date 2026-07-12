from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.schemas import LeadCreate, SearchRequest


SPACE_RE = re.compile(r"\s+")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|[\r\n]+")
WORD_RE = re.compile(r"[a-zа-яё0-9@]+", re.IGNORECASE)

DIRECTORY_HOSTS = {
    "2gis.ru", "zoon.ru", "yell.ru", "flamp.ru",
    "avito.ru", "hh.ru", "superjob.ru", "profi.ru",
}
GENERIC_REJECTIONS = (
    re.compile(r"\bваканси\w*\b|\bрезюме\b|\bработа\s+для\b", re.I),
    re.compile(r"\bкаталог\w*\b|\bагрегатор\w*\b|\bрейтинг\w*\b", re.I),
    re.compile(r"\bдоска\s+объявлен\w*\b|\bсписок\s+компан\w*\b", re.I),
)
STOP_WORDS = {
    "без", "бизнес", "ваш", "ваша", "ваши", "город", "для",
    "есть", "или", "искать", "клиент", "клиенты", "компания",
    "который", "лида", "ниша", "нет", "нужно", "проект", "своя",
    "только", "услуга", "через", "это",
}


@dataclass(frozen=True)
class PainRule:
    label: str
    patterns: tuple[str, ...]
    offer: str


PROFILE_RULES: dict[str, tuple[PainRule, ...]] = {
    "beauty_expert": (
        PainRule(
            "Запись ведётся вручную через сообщения",
            (
                r"(?:для\s+записи|запис\w*).{0,55}(?:пишите|директ|direct|whatsapp|ватсап|telegram|телеграм|личн\w*\s+сообщ)",
                r"(?:пишите|директ|direct|whatsapp|ватсап|telegram|телеграм).{0,55}(?:запис\w*|при[её]м)",
            ),
            "онлайн-запись или Telegram-бот с автоматическими напоминаниями",
        ),
        PainRule(
            "Нет отдельного сайта",
            (r"\bнет\s+(?:своего\s+)?сайт\w*\b", r"\bбез\s+сайт\w*\b"),
            "компактный сайт с услугами, ценами и онлайн-записью",
        ),
    ),
    "trading_finance_content": (
        PainRule(
            "Нет понятной системы риск-менеджмента",
            (
                r"\bбез\s+риск[-\s]?менеджмент\w*\b",
                r"\bнет\s+(?:систем\w*\s+)?риск[-\s]?менеджмент\w*\b",
            ),
            "образовательный модуль по риск-менеджменту без обещаний доходности",
        ),
        PainRule(
            "Аналитика выполняется вручную",
            (r"\bручн\w*.{0,45}\bаналитик\w*\b", r"\bаналитик\w*.{0,45}\bвручн\w*\b"),
            "автоматизация сбора данных и аналитический дашборд",
        ),
    ),
    "marketing_strategy": (
        PainRule(
            "Нет выстроенной воронки продаж",
            (r"\bнет\s+(?:понятн\w*\s+|выстроен\w*\s+)?воронк\w*\b", r"\bзаявк\w*.{0,55}\bтеря\w*\b"),
            "аудит и построение воронки продаж",
        ),
        PainRule(
            "Нет сквозной аналитики",
            (r"\bнет\s+(?:сквозн\w*\s+)?аналитик\w*\b", r"\bне\s+отслеж\w*.{0,45}\bконверси\w*\b"),
            "настройка аналитики, целей и отчёта по воронке",
        ),
    ),
    "smm": (
        PainRule(
            "Контент публикуется нерегулярно",
            (
                r"\bнерегулярн\w*.{0,35}\bпубликац\w*\b",
                r"\bредк\w*.{0,35}\bпубликац\w*\b",
                r"\bдавно\s+не\s+(?:было\s+)?(?:пост|публикац|контент)",
            ),
            "контент-стратегия и регулярное ведение",
        ),
        PainRule(
            "Нет коротких видео",
            (
                r"\bнет\s+(?:коротк\w*\s+)?(?:видео|ролик|reels|shorts)\b",
                r"\bбез\s+(?:reels|shorts|коротк\w*\s+видео)\b",
                r"\bтолько\s+длинн\w*\s+видео\b",
                r"\b(?:shorts|reels|коротк\w*\s+видео).{0,25}\bнет\b",
            ),
            "пакет Reels/Shorts с контент-планом",
        ),
        PainRule(
            "В публикациях нет понятного призыва к действию",
            (
                r"\bнет\s+(?:понятн\w*\s+)?(?:cta|призыв\w*\s+к\s+действ)",
                r"\bбез\s+(?:cta|призыв\w*\s+к\s+действ)",
            ),
            "контент с понятными CTA и маршрутом до заявки",
        ),
    ),
    "software_development": (
        PainRule(
            "Заявки обрабатываются вручную",
            (
                r"\bзаявк\w*.{0,55}\bвручн\w*\b",
                r"\bручн\w*.{0,55}\bзаявк\w*\b",
                r"(?:напишите|пишите).{0,45}(?:менеджер|whatsapp|телеграм|telegram)",
            ),
            "форма квалификации, CRM-интеграция и автоматические уведомления",
        ),
        PainRule(
            "Сайт неудобен на мобильных устройствах",
            (
                r"\bнет\s+мобильн\w*\s+верс\w*\b",
                r"\bне\s+адаптирован\w*.{0,45}\bмобильн\w*\b",
            ),
            "адаптивная версия сайта и исправление мобильного UX",
        ),
        PainRule(
            "Нет интеграции с CRM",
            (r"\bнет\s+(?:интеграц\w*\s+с\s+)?crm\b", r"\bбез\s+crm\b"),
            "CRM-интеграция и автоматизация передачи заявок",
        ),
    ),
    "video_editing": (
        PainRule(
            "Длинные видео не перерабатываются в короткие ролики",
            (
                r"\bдлинн\w*\s+видео.{0,65}\bбез\s+(?:нарезок|shorts|reels|клип)",
                r"\bнет\s+(?:нарезок|shorts|reels).{0,55}\bиз\s+(?:выпуск|видео|подкаст)",
                r"\bтолько\s+длинн\w*\s+(?:видео|выпуск|подкаст)",
            ),
            "нарезка Shorts/Reels из существующих длинных выпусков",
        ),
        PainRule(
            "В видео отсутствуют субтитры",
            (r"\bнет\s+субтитр\w*\b", r"\bбез\s+субтитр\w*\b"),
            "монтаж с динамическими субтитрами и визуальными акцентами",
        ),
        PainRule(
            "Видео публикуются нерегулярно",
            (
                r"\bнерегулярн\w*.{0,40}\b(?:видео|ролик|выпуск)",
                r"\bредк\w*.{0,35}\b(?:видео|ролик|выпуск)",
            ),
            "пакет регулярного монтажа на месяц",
        ),
    ),
    "marketplace_card_design": (
        PainRule(
            "На карточке товара нет инфографики",
            (r"\bнет\s+инфографик\w*\b", r"\bбез\s+инфографик\w*\b"),
            "редизайн первой фотографии и комплекта инфографики",
        ),
        PainRule(
            "Преимущества товара не показаны визуально",
            (
                r"\bне\s+показан\w*.{0,45}\bпреимуществ\w*\b",
                r"\bтолько\s+товар\s+на\s+(?:белом|однотонном)\s+фон\w*\b",
            ),
            "визуальная упаковка преимуществ товара в карточке",
        ),
        PainRule(
            "Карточки не выдержаны в едином стиле",
            (r"\bнет\s+(?:един\w*\s+)?стил\w*\b", r"\bразн\w*.{0,35}\bстил\w*\b"),
            "единая дизайн-система для карточек магазина",
        ),
    ),
}


from app.services.niche_profile_catalog_part5 import build_part5_pain_rules

PROFILE_RULES.update(build_part5_pain_rules(PainRule))


def clean_text(value: Any) -> str:
    return SPACE_RE.sub(" ", str(value or "")).strip()


def split_sentences(value: Any) -> list[str]:
    return [
        clean_text(part).strip(' "\'«»')
        for part in SENTENCE_RE.split(clean_text(value))
        if clean_text(part)
    ]


def shorten(value: Any, limit: int = 220) -> str:
    text = clean_text(value).strip(' "\'«»')
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _evidence_text(lead: LeadCreate) -> str:
    return clean_text(" ".join(str(value or "") for value in (
        lead.name, lead.description, lead.pain_points, lead.source_url, lead.website_url,
    )))


def _target_keywords(value: str) -> list[str]:
    return [
        word for word in WORD_RE.findall(clean_text(value).lower().replace("ё", "е"))
        if len(word) >= 4 and word not in STOP_WORDS
    ]


def _sentence_for_keywords(sentences: list[str], keywords: list[str]) -> str | None:
    if not keywords:
        return None
    for sentence in sentences:
        lowered = sentence.lower().replace("ё", "е")
        matches = [
            word for word in keywords
            if word in lowered or (len(word) >= 6 and word[:5] in lowered)
        ]
        if len(set(matches)) >= (1 if len(keywords) == 1 else 2):
            return sentence
    return None


def _detect_profile_rule(text: str, profile_code: str) -> tuple[str, str, str] | None:
    for sentence in split_sentences(text):
        for rule in PROFILE_RULES.get(profile_code, ()):
            if any(re.search(pattern, sentence, re.I) for pattern in rule.patterns):
                return rule.label, shorten(sentence), rule.offer
    return None


def _confirmed(value: str | None) -> bool:
    lowered = clean_text(value).lower()
    return bool(lowered) and lowered not in {"не найден", "не определено"} and "подтверждение:" in lowered


def analyze_universal_lead(
    lead: LeadCreate,
    req: SearchRequest,
    context: dict[str, Any],
) -> LeadCreate:
    evidence = _evidence_text(lead)
    detected = _detect_profile_rule(evidence, clean_text(context.get("profile_code")))

    if detected is None:
        keywords = _target_keywords(" ".join([
            req.target_pain,
            *[str(item) for item in context.get("pain_signals", [])],
        ]))
        sentence = _sentence_for_keywords(split_sentences(evidence), keywords)
        if sentence:
            label = clean_text(req.target_pain)
            if not label:
                signals = context.get("pain_signals") or []
                label = clean_text(signals[0] if signals else "Выявлена потребность")
            label = label.rstrip(" .")
            if label:
                label = label[0].upper() + label[1:]
            offers = context.get("offer_examples") or req.services
            detected = label, shorten(sentence), clean_text(offers[0] if offers else "")

    if detected:
        label, sentence, offer = detected
        lead.pain_points = f"{label}\nПодтверждение: «{sentence}»"
        if offer:
            lead.suggested_offer = offer
    elif not _confirmed(lead.pain_points):
        lead.pain_points = "Боль не подтверждена явным текстом"
        offers = [
            *[clean_text(item) for item in req.services if clean_text(item)],
            *[clean_text(item) for item in context.get("offer_examples", []) if clean_text(item)],
        ]
        if offers and not clean_text(lead.suggested_offer):
            lead.suggested_offer = offers[0]
    return lead


def _host(value: str | None) -> str:
    try:
        return urlparse(value or "").netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def _token_matches(text: str, token: str) -> bool:
    hay = text.lower().replace("ё", "е")
    needle = token.lower().replace("ё", "е")
    return needle in hay or (len(needle) >= 6 and needle[:5] in hay)


def score_universal_lead(
    lead: LeadCreate,
    req: SearchRequest,
    context: dict[str, Any],
) -> tuple[int, str]:
    evidence = _evidence_text(lead)
    host = _host(lead.source_url or lead.website_url)

    if host in DIRECTORY_HOSTS:
        return 0, f"жесткий отказ: источник-каталог {host}"
    if any(pattern.search(evidence) for pattern in GENERIC_REJECTIONS):
        return 0, "жесткий отказ: каталог, вакансия или агрегатор"

    lowered = evidence.lower()
    for raw in re.split(r"[,;\n]+", req.exclude or ""):
        term = clean_text(raw).lower()
        if len(term) >= 4 and term in lowered:
            return 0, f"жесткий отказ: найдено исключение «{term}»"

    score = 0
    reasons: list[str] = []
    target_text = " ".join([
        req.niche,
        req.target_type,
        clean_text(context.get("profile_name")),
        clean_text(context.get("custom_niche")),
    ])
    target_tokens = list(dict.fromkeys(_target_keywords(target_text)))
    matched = [token for token in target_tokens if _token_matches(evidence, token)]

    if matched:
        score += 25
        reasons.append("подтверждён целевой тип клиента +25")
    else:
        score -= 20
        reasons.append("целевой тип клиента подтверждён слабо -20")

    city = clean_text(req.city)
    if city and _token_matches(evidence, city):
        score += 10
        reasons.append("подтверждён регион +10")

    if len(WORD_RE.findall(clean_text(lead.name))) >= 2 or "@" in clean_text(lead.name):
        score += 15
        reasons.append("есть различимая компания или автор +15")
    else:
        score += 5
        reasons.append("идентичность определена частично +5")

    direct = bool(lead.email or lead.phone or lead.whatsapp)
    social = bool(lead.telegram_url or lead.instagram_url or lead.vk_url or lead.tiktok_url or lead.youtube_url)
    if direct:
        score += 15
        reasons.append("есть прямой контакт +15")
    elif social:
        score += 8
        reasons.append("есть социальный профиль +8")
    else:
        score -= 10
        reasons.append("контакт не найден -10")

    if host and host not in DIRECTORY_HOSTS:
        score += 10
        reasons.append("есть собственный источник или профиль +10")

    confirmed = _confirmed(lead.pain_points)
    if confirmed:
        score += 25
        reasons.append("боль подтверждена точной цитатой +25")
    else:
        score -= 10
        reasons.append("боль не подтверждена -10")

    if clean_text(lead.suggested_offer):
        score += 10
        reasons.append("сформирован релевантный оффер +10")

    maximum = 100
    if not matched:
        maximum = min(maximum, 55)
        reasons.append("потолок 55: целевой тип клиента не подтверждён")
    if not confirmed:
        maximum = min(maximum, 70)
        reasons.append("потолок 70: нет точного подтверждения боли")
    if not direct:
        maximum = min(maximum, 80)
        reasons.append("потолок 80: нет прямого контакта")

    return max(0, min(100, min(score, maximum))), "; ".join(reasons)


def _pain_parts(value: str | None) -> tuple[str, str]:
    text = clean_text(value)
    if "Подтверждение:" not in text:
        return text or "потребность пока не подтверждена", ""
    label, evidence = text.split("Подтверждение:", 1)
    return clean_text(label), clean_text(evidence).strip(" «»\"'")


def _subject(lead: LeadCreate) -> str:
    name = clean_text(lead.name)
    for separator in (" | ", " — ", " - ", " • "):
        if separator in name:
            name = name.split(separator, 1)[0].strip()
    return shorten(name or "ваш проект", 70)


def generate_universal_outreach(
    lead: LeadCreate,
    context: dict[str, Any],
) -> dict[str, str]:
    subject = _subject(lead)
    pain, evidence = _pain_parts(lead.pain_points)
    offer = clean_text(lead.suggested_offer)
    if not offer:
        examples = context.get("offer_examples") or []
        offer = clean_text(examples[0] if examples else "решение под вашу задачу")

    observation = pain
    if evidence:
        observation = f"{pain.lower()} — на странице указано: «{shorten(evidence, 130)}»"

    profile_name = clean_text(
        context.get("profile_name") or context.get("custom_niche") or "вашего направления"
    )
    return {
        "soft": (
            f"Здравствуйте! Посмотрел проект «{subject}» и обратил внимание, что {observation}. "
            f"Могу предложить {offer}. Могу показать короткий вариант решения именно под ваш проект — актуально?"
        ),
        "business": (
            f"Здравствуйте! Изучил «{subject}». Обнаруженная точка роста: {observation}. "
            f"Для направления «{profile_name}» предлагаю {offer}. "
            "Если задача актуальна, пришлю короткий план внедрения."
        ),
        "short": (
            f"Здравствуйте! Увидел у «{subject}», что {observation}. "
            f"Могу помочь через {offer}. Показать короткий пример?"
        ),
    }
