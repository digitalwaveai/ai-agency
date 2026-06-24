from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.schemas import SearchRequest


DIRECTORY_HOSTS = {
    "2gis.ru",
    "yandex.ru",
    "yandex.com",
    "zoon.ru",
    "prodoctorov.ru",
    "docdoc.ru",
    "napopravku.ru",
    "yell.ru",
    "flamp.ru",
    "otzovik.com",
    "irecommend.ru",
    "spr.ru",
    "orgpage.ru",
    "profi.ru",
    "youdo.com",
    "uslugi.yandex.ru",
    "maps.google.com",
    "google.com",
}

JOB_HOSTS = {
    "hh.ru",
    "superjob.ru",
    "rabota.ru",
    "zarplata.ru",
    "career.habr.com",
}

CONTENT_HOSTS = {
    "ru.wikipedia.org",
    "wikipedia.org",
    "dzen.ru",
    "vc.ru",
    "pikabu.ru",
}

SOCIAL_HOSTS = {
    "instagram.com",
    "vk.com",
    "t.me",
    "telegram.me",
    "tiktok.com",
    "youtube.com",
    "youtu.be",
}

BOOKING_HOSTS = {
    "yclients.com",
    "dikidi.net",
    "alteg.io",
    "altegio.com",
}

PLACEHOLDER_HOSTS = {
    "example.com",
    "www.example.com",
}

BAD_PATH_RE = re.compile(
    r"(?:^|/)(?:reviews?|otzyvy?|отзывы?|rating|ratings|рейтинг|"
    r"catalog|каталог|articles?|статьи?|blog|блог|news|новости|"
    r"vacanc(?:y|ies)|вакансии?|jobs?|работа)(?:/|$)",
    re.IGNORECASE,
)

CHAIN_RE = re.compile(
    r"\b(?:федеральн\w*\s+сеть|сеть\s+(?:салон\w*|клиник\w*|студи\w*|центр\w*)|"
    r"франшиз\w*|\d+\s+филиал\w*|филиал(?:ы|ов|ами)?\b|"
    r"более\s+\d+\s+(?:салон\w*|клиник\w*|филиал\w*)|"
    r"в\s+\d+\s+город\w*|по\s+всей\s+россии|крупнейш\w*\s+сеть)\b",
    re.IGNORECASE,
)

DIRECTORY_TEXT_RE = re.compile(
    r"\b(?:каталог|агрегатор|рейтинг|топ[-\s]?\d+|лучшие\s+\d*|"
    r"отзывы\s+(?:о|об|на)|сравн(?:ить|ение)|список\s+(?:врачей|мастеров|специалистов)|"
    r"найти\s+(?:врача|мастера|специалиста)|все\s+клиники|все\s+салоны)\b",
    re.IGNORECASE,
)

JOB_TEXT_RE = re.compile(
    r"\b(?:ваканси\w*|требуется|работа\s+(?:для|косметолог)|зарплат\w*|резюме)\b",
    re.IGNORECASE,
)

TRAINING_TEXT_RE = re.compile(
    r"\b(?:курс\w*\s+(?:косметолог|бровист|визажист|маникюр)|"
    r"обучени\w*\s+(?:косметолог|бровист|визажист|маникюр)|"
    r"школа\s+(?:косметолог|красоты|мастеров)|вебинар|повышение\s+квалификации)\b",
    re.IGNORECASE,
)

ARTICLE_TEXT_RE = re.compile(
    r"\b(?:что\s+такое|как\s+выбрать|советы|статья|обзор|новости|"
    r"энциклопедия|инструкция|сколько\s+зарабатывает)\b",
    re.IGNORECASE,
)

SMALL_BUSINESS_RE = re.compile(
    r"\b(?:частн\w*|мастер|специалист|кабинет|студия|салон|"
    r"принимаю|веду\s+при[её]м|запись|процедур\w*|услуг\w*)\b",
    re.IGNORECASE,
)

CONTACT_OR_ACTION_RE = re.compile(
    r"(?:\+?\d[\d\s().-]{8,}\d|@[a-z0-9_.-]+|whatsapp|ватсап|"
    r"telegram|телеграм|direct|директ|личн\w*\s+сообщ\w*|записаться|запись)",
    re.IGNORECASE,
)

CITY_ALIASES = {
    "москва": ("москва", "москве", "москвы", "московск"),
    "санкт-петербург": ("санкт-петербург", "петербург", "спб", "питер"),
    "санкт петербург": ("санкт-петербург", "петербург", "спб", "питер"),
    "новосибирск": ("новосибирск", "новосибирске", "новосибирск"),
    "екатеринбург": ("екатеринбург", "екатеринбурге"),
    "казань": ("казань", "казани"),
}

NICHE_GROUPS = {
    "косметолог": (
        "косметолог",
        "косметология",
        "эстетист",
        "косметик",
        "инъекцион",
        "уход за лицом",
    ),
    "бров": ("бровист", "брови", "brow"),
    "lash": ("lash", "лэш", "ресниц"),
    "ресниц": ("lash", "лэш", "ресниц"),
    "маникюр": ("маникюр", "ногт", "nail"),
    "визаж": ("визаж", "макияж", "makeup"),
    "парикмах": ("парикмах", "волос", "hair"),
    "массаж": ("массаж", "массажист"),
}


@dataclass(frozen=True)
class QualityDecision:
    accepted: bool
    score: int
    reasons: tuple[str, ...]


def normalize_text(value: str | None) -> str:
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"[^a-zа-я0-9@+./:-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def host_of(url: str | None) -> str:
    if not url:
        return ""
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def is_host_in(host: str, hosts: set[str]) -> bool:
    return any(host == item or host.endswith("." + item) for item in hosts)


def is_social_host(host: str) -> bool:
    return is_host_in(host, SOCIAL_HOSTS)


def is_booking_host(host: str) -> bool:
    return is_host_in(host, BOOKING_HOSTS)


def is_placeholder_host(host: str) -> bool:
    return is_host_in(host, PLACEHOLDER_HOSTS)


def hard_bad_host(url: str | None) -> bool:
    host = host_of(url)
    return (
        is_host_in(host, DIRECTORY_HOSTS)
        or is_host_in(host, JOB_HOSTS)
        or is_host_in(host, CONTENT_HOSTS)
    )


def hard_bad_path(url: str | None) -> bool:
    if not url:
        return True
    parsed = urlparse(url)
    return bool(BAD_PATH_RE.search(parsed.path or ""))


def niche_terms(niche: str) -> tuple[str, ...]:
    normalized = normalize_text(niche)
    terms: list[str] = []

    for key, aliases in NICHE_GROUPS.items():
        if key in normalized:
            terms.extend(aliases)

    terms.extend(
        token
        for token in re.findall(r"[a-zа-я0-9]+", normalized)
        if len(token) >= 4
    )

    unique: list[str] = []
    seen: set[str] = set()
    for term in terms:
        cleaned = normalize_text(term)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)

    return tuple(unique)


def city_terms(city: str) -> tuple[str, ...]:
    normalized = normalize_text(city)
    aliases = list(CITY_ALIASES.get(normalized, (normalized,)))

    compact = normalized.replace("-", " ")
    if len(compact) >= 6:
        aliases.append(compact[:-1])
    if len(compact) >= 8:
        aliases.append(compact[:5])

    return tuple(dict.fromkeys(term for term in aliases if term))


def text_matches_niche(text: str, niche: str) -> bool:
    normalized = normalize_text(text)
    return any(term in normalized for term in niche_terms(niche))


def text_matches_city(text: str, city: str) -> bool:
    normalized = normalize_text(text)
    return any(term in normalized for term in city_terms(city))


def _custom_exclusion_match(text: str, exclude: str) -> str | None:
    normalized_text = normalize_text(text)

    for raw_item in re.split(r"[,;\n]+", exclude or ""):
        item = normalize_text(raw_item)
        if len(item) >= 4 and item in normalized_text:
            return item

    return None


def hard_rejection_reason(text: str, url: str, exclude: str = "") -> str | None:
    normalized = normalize_text(text)

    if hard_bad_host(url):
        return "каталог, агрегатор, отзывы, вакансии или информационный сайт"

    if hard_bad_path(url):
        return "служебная, обзорная, каталожная или вакансионная страница"

    if CHAIN_RE.search(normalized):
        return "сеть, франшиза или много филиалов"

    if DIRECTORY_TEXT_RE.search(normalized):
        return "каталог, рейтинг, отзывы или подборка"

    if JOB_TEXT_RE.search(normalized):
        return "вакансия или поиск работы"

    if TRAINING_TEXT_RE.search(normalized):
        return "обучение или курс вместо потенциального клиента"

    if ARTICLE_TEXT_RE.search(normalized):
        return "статья, обзор или информационная публикация"

    custom = _custom_exclusion_match(normalized, exclude)
    if custom:
        return f"совпадение с исключением: {custom}"

    return None


def assess_candidate_text(
    *,
    title: str,
    snippet: str,
    url: str,
    niche: str,
    city: str,
    exclude: str = "",
    require_city: bool = False,
) -> QualityDecision:
    combined = " ".join(part for part in (title, snippet, url) if part)
    normalized = normalize_text(combined)
    rejection = hard_rejection_reason(normalized, url, exclude)

    if rejection:
        return QualityDecision(False, 0, (rejection,))

    if not text_matches_niche(normalized, niche):
        return QualityDecision(False, 0, ("не подтверждена запрошенная ниша",))

    host = host_of(url)
    city_match = text_matches_city(normalized, city)
    local_signal = bool(SMALL_BUSINESS_RE.search(normalized))
    action_signal = bool(CONTACT_OR_ACTION_RE.search(normalized))
    social_or_booking = is_social_host(host) or is_booking_host(host)

    if require_city and not city_match:
        return QualityDecision(False, 0, ("не подтвержден запрошенный город",))

    score = 45
    reasons = ["подтверждена ниша +45"]

    if city_match:
        score += 20
        reasons.append("подтвержден город +20")
    else:
        score -= 10
        reasons.append("город не подтвержден -10")

    if local_signal:
        score += 15
        reasons.append("есть признаки локального бизнеса +15")

    if action_signal:
        score += 10
        reasons.append("есть запись или прямой контакт +10")

    if social_or_booking:
        score += 10
        reasons.append("найден профиль бизнеса +10")

    accepted = score >= 50 and (local_signal or action_signal or social_or_booking)

    if not accepted:
        reasons.append("недостаточно признаков реального локального бизнеса")

    return QualityDecision(
        accepted,
        max(0, min(100, score)),
        tuple(reasons),
    )


def canonical_result_key(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/").lower()

    if is_social_host(host) or is_booking_host(host):
        return f"{host}{path}"

    return host


def rank_search_results(results, req: SearchRequest, max_results: int):
    ranked = []
    seen_keys: set[str] = set()

    for result in results:
        key = canonical_result_key(result.url)
        if not key or key in seen_keys:
            continue

        decision = assess_candidate_text(
            title=result.title,
            snippet=result.snippet,
            url=result.url,
            niche=req.niche,
            city=req.city,
            exclude=req.exclude,
            require_city=False,
        )

        if not decision.accepted:
            continue

        seen_keys.add(key)
        result.quality_score = decision.score
        result.quality_reason = "; ".join(decision.reasons)
        ranked.append(result)

    ranked.sort(
        key=lambda item: (
            item.quality_score,
            len(item.snippet or ""),
        ),
        reverse=True,
    )

    return ranked[:max_results]


def pain_is_confirmed(value: str | None) -> bool:
    normalized = normalize_text(value)
    return bool(
        normalized
        and normalized != "не найден"
        and not normalized.startswith("выбранная боль не подтверждена")
    )


def offer_is_relevant(value: str | None) -> bool:
    normalized = normalize_text(value)
    return bool(
        normalized
        and not normalized.startswith("не предлагать автоматически")
    )
