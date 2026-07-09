from __future__ import annotations

import re

from app.schemas import SearchRequest


BEAUTY_MARKERS = (
    "beauty",
    "косметолог",
    "косметология",
    "эстетист",
    "маникюр",
    "бровист",
    "ресниц",
    "салон красоты",
    "массаж",
)


def _normalize(value: str) -> str:
    return value.lower().replace("ё", "е").strip()


def _is_beauty_request(req: SearchRequest) -> bool:
    haystack = " ".join(
        [
            req.niche,
            req.target_type,
            req.target_pain,
            *req.services,
        ]
    )
    normalized = _normalize(haystack)
    return any(marker in normalized for marker in BEAUTY_MARKERS)


def _beauty_negative_terms(niche: str = "") -> str:
    terms = (
        "-вакансии -работа -курс -обучение -рейтинг -отзывы "
        "-каталог -франшиза -сеть -филиал -холдинг -агрегатор "
        "-pinterest -tgstat -livejournal -facebook -vkvideo -rutube "
        '-"ищу модель" -"ищу мастера" -"день открытых дверей" '
        '-"специальные предложения" -"статьи сообщества" '
        '-"повторная запись" -семинар -вебинар -edu'
    )

    normalized_niche = _normalize(niche)
    if any(
        marker in normalized_niche
        for marker in ("косметолог", "косметология", "эстетист")
    ):
        terms += (
            ' -"наращивание ресниц" -лешмейкер -lash '
            '-визажист -макияж -makeup -бровист '
            '-маникюр -педикюр -парикмахер'
        )
    return terms


def _beauty_queries(req: SearchRequest) -> list[str]:
    niche = req.niche.strip()
    city = req.city.strip()
    negative = _beauty_negative_terms(niche)

    queries = [
        f"{niche} {city} частный мастер запись {negative}",
        f'"частный {niche}" "{city}" запись {negative}',
        f'"{niche}" "{city}" кабинет запись {negative}',
        f'"{niche}" "{city}" "для записи пишите" {negative}',
        f'"{niche}" "{city}" запись WhatsApp {negative}',
        f'"врач-{niche}" "{city}" частный прием {negative}',
        f'site:vk.com "{niche}" "{city}" запись {negative}',
        f'site:instagram.com "{niche}" "{city}" запись {negative}',
    ]

    target = _normalize(req.target_pain)

    if any(word in target for word in ("прайс", "цен", "публикац", "пост")):
        queries.extend(
            [
                f'"{niche}" "{city}" "прайс в постах" {negative}',
                f'site:vk.com "{niche}" "{city}" прайс запись {negative}',
            ]
        )

    if any(
        word in target
        for word in ("личн", "сообщ", "директ", "whatsapp", "телеграм")
    ):
        queries.extend(
            [
                f'"{niche}" "{city}" "запись в личные сообщения" {negative}',
                f'"{niche}" "{city}" "запись через WhatsApp" {negative}',
            ]
        )

    if any(word in target for word in ("нет сайта", "без сайта", "соцсет")):
        queries.extend(
            [
                f'site:vk.com "{niche}" "{city}" услуги запись {negative}',
                f'site:t.me "{niche}" "{city}" запись {negative}',
            ]
        )

    return list(dict.fromkeys(query.strip() for query in queries if query.strip()))


def _safe_phrase(value: str) -> str:
    return re.sub(r'["\r\n]+', " ", value).strip()


def _exclude_terms(value: str) -> str:
    defaults = [
        "вакансии",
        "работа",
        "резюме",
        "каталог",
        "агрегатор",
        "рейтинг",
        "отзывы",
        "франшиза",
        "тендер",
    ]
    custom = [
        item.strip()
        for item in re.split(r"[,;\n]+", value or "")
        if item.strip()
    ]

    terms: list[str] = []
    for item in [*defaults, *custom]:
        cleaned = _safe_phrase(item)
        if not cleaned:
            continue
        if " " in cleaned:
            terms.append(f'-"{cleaned}"')
        else:
            terms.append(f"-{cleaned}")
    return " ".join(dict.fromkeys(terms))


def _target_variants(req: SearchRequest) -> list[str]:
    values: list[str] = []
    for source in (req.niche, req.target_type):
        for item in re.split(r"[,;/|]+", source or ""):
            cleaned = _safe_phrase(item)
            if cleaned and cleaned.lower() not in {"другое", "оба", "оба варианта"}:
                values.append(cleaned)
    return list(dict.fromkeys(values))[:4]


def _pain_phrases(value: str) -> list[str]:
    phrases = [
        _safe_phrase(item)
        for item in re.split(r"[,;\n]+", value or "")
        if _safe_phrase(item)
    ]
    return list(dict.fromkeys(phrases))[:4]


def _generic_queries(req: SearchRequest) -> list[str]:
    location = _safe_phrase(req.city or req.country or "онлайн")
    targets = _target_variants(req)
    pains = _pain_phrases(req.target_pain)
    negative = _exclude_terms(req.exclude)

    if not targets:
        targets = ["бизнес"]

    queries: list[str] = []

    for target in targets[:3]:
        queries.extend(
            [
                f'"{target}" "{location}" контакты {negative}',
                f'"{target}" "{location}" услуги {negative}',
                f'"{target}" "{location}" официальный сайт {negative}',
                f'site:vk.com "{target}" "{location}" {negative}',
                f'site:t.me "{target}" "{location}" {negative}',
                f'site:instagram.com "{target}" "{location}" {negative}',
                f'site:youtube.com "{target}" "{location}" {negative}',
            ]
        )

    primary = targets[0]
    for pain in pains:
        queries.append(f'"{primary}" "{location}" "{pain}" {negative}')

    normalized_pain = _normalize(req.target_pain)
    if any(word in normalized_pain for word in ("сообщ", "директ", "ручн", "заяв")):
        queries.extend(
            [
                f'"{primary}" "{location}" "напишите нам" {negative}',
                f'"{primary}" "{location}" "оставить заявку" {negative}',
                f'"{primary}" "{location}" "связаться" {negative}',
            ]
        )

    if any(word in normalized_pain for word in ("контент", "публикац", "ролик", "видео")):
        queries.extend(
            [
                f'site:youtube.com "{primary}" "{location}" {negative}',
                f'site:vk.com "{primary}" "{location}" видео {negative}',
                f'site:t.me "{primary}" "{location}" контент {negative}',
            ]
        )

    return list(dict.fromkeys(query.strip() for query in queries if query.strip()))[:30]


def build_search_queries(req: SearchRequest) -> list[str]:
    if _is_beauty_request(req):
        return _beauty_queries(req)
    return _generic_queries(req)
