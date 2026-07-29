from __future__ import annotations

import asyncio
import json
import re
from functools import wraps
from typing import Any
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from .models import Lead

TARGET_SEPARATOR = " ||| "
PRIORITY_SEPARATOR = " ||+ "

WORD_RE = re.compile(r"[a-zа-яё0-9]+(?:-[a-zа-яё0-9]+)?", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?7|8)[\d\s().-]{8,}\d")

STOP_WORDS = {
    "и", "или", "а", "но", "в", "во", "на", "по", "с", "со", "к", "ко", "у",
    "из", "для", "от", "до", "под", "над", "при", "через", "без", "про", "об",
    "это", "этот", "эта", "эти", "как", "что", "чтобы", "который", "которые",
    "которым", "которых", "где", "когда", "уже", "еще", "ещё", "сейчас",
    "нужен", "нужна", "нужно", "нужны", "хочет", "хотят", "может", "могут",
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with",
}

GENERIC_ROOTS = {
    "владел", "бизнес", "компан", "клиент", "задач", "процесс", "услуг",
    "проект", "продукт", "решен", "работ", "развит", "рост", "план",
    "действ", "повтор", "ручн", "новый", "готов", "систем", "сервис",
    "активн", "входящ", "холодн", "заявк", "заяво", "запис", "мессен",
    "поток", "сайт",
    "company", "business", "owner", "client", "service", "project", "product",
}

HARD_NEGATIVE_TERMS = {
    "казино", "casino", "gambling", "букмекер", "ставки", "ставка на спорт",
    "betting", "bet365", "1xbet", "pin-up", "poker", "покер", "слоты", "slots",
    "лотерея", "jackpot", "джекпот", "вулкан", "азартные игры", "онлайн казино",
    "porn", "порно", "escort", "эскорт",
}

GENERIC_CONTENT_TERMS = {
    "официальный сайт города", "городской портал", "администрация города",
    "правительство области", "мэрия", "муниципальное образование",
    "новости города", "городские новости", "погода", "афиша города",
    "энциклопедия", "википедия", "wiki", "вакансии", "резюме", "поиск работы",
    "работа в городе", "каталог сайтов", "каталог организаций", "справочник города",
    "карта города", "история города", "достопримечательности",
}

ARTICLE_MARKERS = {
    "топ-", "топ ", "рейтинг", "лучшие ", "список ", "обзор ", "статья ",
    "что такое", "сколько стоит", "инструкция",
    "пошагов", "новости", "вакансии", "сервисы для", "сайты для",
    "идеи для", "советы", "гайд", "чек-лист", "вебинар", "курс ",
    "анкета", "опрос",
}

ARTICLE_QUESTION_RE = re.compile(
    r"^(?:как|почему|зачем|сколько)\s+"
    r"(?:открыть|выбрать|создать|сделать|запустить|увеличить|продвигать|"
    r"настроить|салон\w*|мастер\w*|бизнес\w*|компан\w*|эксперт\w*|"
    r"селлер\w*)\b",
    re.IGNORECASE,
)

BLOCKED_HOSTS = {
    "wikipedia.org", "ru.wikipedia.org", "hh.ru", "superjob.ru", "rabota.ru",
    "trudvsem.ru", "ria.ru", "tass.ru", "rbc.ru", "vc.ru", "dzen.ru",
    "zen.yandex.ru", "habr.com", "medium.com", "pikabu.ru", "2gis.ru",
    "zoon.ru", "yell.ru", "spravker.ru", "forms.gle", "docs.google.com",
    "typeform.com", "survio.com",
}

CONTENT_PATH_MARKERS = {
    "article", "articles", "blog", "blogs", "guide", "guides", "journal",
    "journals", "news", "novosti", "post", "posts", "publication",
    "publications", "rating", "ratings", "review", "reviews", "statya",
    "stati", "top", "tops", "vacancies", "vacancy", "vakansii", "wiki",
    "webinar", "webinars", "course", "courses", "kurs", "quiz", "survey",
    "anketa", "questionnaire", "form", "forms",
}

SOCIAL_PROFILE_HOSTS = {
    "instagram.com",
    "vk.com",
    "t.me",
    "telegram.me",
    "ok.ru",
}

PROFILE_PAGE_HOSTS = {
    "taplink.cc",
    "dikidi.net",
    "yclients.com",
}

SOCIAL_CONTENT_SEGMENTS = {
    "p", "reel", "reels", "stories", "story", "explore", "tv", "wall",
    "topic", "article", "articles", "video", "videos", "clip", "clips",
    "s", "joinchat", "share", "addstickers",
}

LOCAL_SERVICE_PAGE_MARKERS = {
    "продвижение сайта",
    "продвижение салона",
    "создание сайта",
    "разработка сайта",
    "заказать логотип",
    "заказать фирменный логотип",
    "маркетинг для",
    "реклама для",
    "увеличить прибыль",
    "открыть салон",
}

CATEGORY_GROUPS: tuple[tuple[set[str], set[str]], ...] = (
    (
        {"маркетплейс", "селлер", "seller", "wildberries", "ozon", "вайлдберриз", "вб", "wb", "интернет-магазин"},
        {"маркетплейс", "селлер", "seller", "wildberries", "ozon", "вайлдберриз", "вб", "wb", "интернет-магазин", "магазин", "карточк", "товар", "бренд"},
    ),
    (
        {"автоматизац", "нейросет", "искусственн интеллект", "ai", "ии", "чат-бот", "chatbot"},
        {"автоматизац", "нейросет", "искусственн интеллект", "ai", "ии", "чат-бот", "chatbot", "crm", "интеграц", "бот"},
    ),
    (
        {"дизайн", "инфограф", "визуал", "брендинг", "графическ"},
        {"дизайн", "инфограф", "визуал", "брендинг", "графическ", "карточк", "креатив"},
    ),
    (
        {"косметолог", "салон красот", "beauty", "бьюти", "маникюр", "бров", "lash", "парикмах"},
        {"косметолог", "салон", "beauty", "бьюти", "маникюр", "бров", "lash", "парикмах", "студи"},
    ),
    (
        {"недвижим", "риелтор", "риэлтор", "застройщик", "строительн"},
        {"недвижим", "риелтор", "риэлтор", "застройщик", "строительн", "жилой комплекс", "агентство недвижимости"},
    ),
    (
        {"юридическ", "адвокат", "бухгалтер", "кадров"},
        {"юридическ", "адвокат", "бухгалтер", "кадров", "налог", "право"},
    ),
    (
        {"онлайн-школ", "образован", "курс", "наставник", "консультант"},
        {"онлайн-школ", "образован", "курс", "наставник", "консультант", "обучен", "школ"},
    ),
)

AUDIENCE_GROUPS: tuple[tuple[set[str], set[str]], ...] = (
    (
        {"селлер", "seller", "продавец", "продавцы", "интернет-магазин"},
        {"селлер", "seller", "продавец", "продавцы", "магазин", "бренд", "производитель", "поставщик", "интернет-магазин"},
    ),
    (
        {"владелец салона", "салон красоты", "косметолог", "бьюти-эксперт"},
        {"салон", "косметолог", "студия", "клиника", "мастер", "beauty", "бьюти"},
    ),
    (
        {"агентство недвижимости", "риелтор", "риэлтор", "застройщик"},
        {"агентство недвижимости", "риелтор", "риэлтор", "застройщик", "жилой комплекс"},
    ),
    (
        {"онлайн-школа", "эксперт", "наставник", "консультант"},
        {"онлайн-школа", "школа", "эксперт", "наставник", "консультант", "образовательный проект"},
    ),
    (
        {"маркетинговое агентство", "digital-агентство", "диджитал-агентство", "отдел продаж", "продюсерский центр"},
        {"маркетинговое агентство", "digital-агентство", "диджитал-агентство", "агентство", "отдел продаж", "продюсерский центр"},
    ),
)

ONLINE_MARKERS = {
    "маркетплейс", "селлер", "seller", "wildberries", "ozon", "интернет-магазин",
    "ai", "ии", "автоматизац", "дизайн", "инфограф", "контент", "маркетинг",
    "разработ", "онлайн", "digital", "it", "бренд",
}


def _normalize(value: object) -> str:
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _root(word: str) -> str:
    word = _normalize(word).strip("-")
    if len(word) >= 9:
        return word[:7]
    if len(word) >= 6:
        return word[:5]
    return word


def _words(value: object) -> list[str]:
    text = _normalize(value).replace("-", " ")
    return [_normalize(item) for item in WORD_RE.findall(text)]


def _important_tokens(value: object) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for word in _words(value):
        if word in STOP_WORDS:
            continue
        root = _root(word)
        if len(root) < 3 and root not in {"ai", "ии", "wb", "вб"}:
            continue
        if any(root.startswith(generic) or generic.startswith(root) for generic in GENERIC_ROOTS):
            continue
        if root not in seen:
            seen.add(root)
            result.append(root)
    return result


def _important_words(value: object) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for word in _words(value):
        if word in STOP_WORDS:
            continue
        root = _root(word)
        if len(root) < 3 and root not in {"ai", "ии", "wb", "вб"}:
            continue
        if any(root.startswith(generic) or generic.startswith(root) for generic in GENERIC_ROOTS):
            continue
        if root not in seen:
            seen.add(root)
            result.append(word)
    return result


def _contains_root(text: str, root: str) -> bool:
    if root in {"ai", "ии", "wb", "вб"}:
        return bool(re.search(rf"(?<![a-zа-я0-9]){re.escape(root)}(?![a-zа-я0-9])", text))
    return any(_root(word) == root for word in _words(text))


def _matches(text: str, roots: list[str]) -> int:
    return sum(1 for root in roots if _contains_root(text, root))


def _split_target(target: str) -> tuple[str, str, str]:
    search_target, priority_separator, priorities = target.partition(
        PRIORITY_SEPARATOR
    )
    primary, target_separator, secondary = search_target.partition(
        TARGET_SEPARATOR
    )
    return (
        primary.strip(),
        secondary.strip() if target_separator else "",
        priorities.strip() if priority_separator else "",
    )


def _phrase_present(text: str, phrase: str) -> bool:
    normalized = _normalize(phrase)
    if normalized in {"ai", "ии", "wb", "вб"}:
        return bool(
            re.search(
                rf"(?<![a-zа-я0-9]){re.escape(normalized)}(?![a-zа-я0-9])",
                text,
            )
        )
    return normalized in text


def _category_aliases(value: str) -> set[str]:
    normalized = _normalize(value)
    aliases: set[str] = set()
    for triggers, group_aliases in CATEGORY_GROUPS:
        if any(_phrase_present(normalized, trigger) for trigger in triggers):
            aliases.update(group_aliases)
    return aliases


def _alias_hits(text: str, aliases: set[str]) -> int:
    return sum(1 for alias in aliases if _phrase_present(text, alias))


def _audience_aliases(value: str) -> set[str]:
    normalized = _normalize(value)
    aliases: set[str] = set()
    for triggers, group_aliases in AUDIENCE_GROUPS:
        if any(_phrase_present(normalized, trigger) for trigger in triggers):
            aliases.update(group_aliases)
    return aliases


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def _path_segments(url: str) -> list[str]:
    try:
        path = unquote(urlparse(url).path).lower()
    except ValueError:
        return []
    return [segment for segment in path.split("/") if segment]


def _blocked_content_path(url: str) -> bool:
    segments = _path_segments(url)
    if segments and segments[-1].endswith(
        (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
    ):
        return True
    if any(segment in CONTENT_PATH_MARKERS for segment in segments):
        return True
    path = "/".join(segments)
    return any(
        marker in path
        for marker in (
            "kak-otkryt",
            "kak-vybrat",
            "kak-uvelichit",
            "poshagov",
            "servisov-dlya",
            "site-for",
            "sayt-dlya",
        )
    )


def _direct_social_profile(url: str) -> bool:
    host = _host(url)
    if host not in SOCIAL_PROFILE_HOSTS:
        return False
    segments = _path_segments(url)
    if len(segments) != 1:
        return False
    segment = segments[0]
    if segment in SOCIAL_CONTENT_SEGMENTS:
        return False
    return not any(
        segment.startswith(prefix)
        for prefix in ("wall-", "topic-", "video-", "clip-")
    )


def _business_surface(url: str) -> bool:
    host = _host(url)
    if not host or host in BLOCKED_HOSTS or _blocked_content_path(url):
        return False
    if host in SOCIAL_PROFILE_HOSTS:
        return _direct_social_profile(url)
    segments = _path_segments(url)
    if host in PROFILE_PAGE_HOSTS or any(
        host.endswith(f".{profile_host}") for profile_host in PROFILE_PAGE_HOSTS
    ):
        return bool(segments)
    if not segments:
        return True
    if len(segments) == 1:
        return True
    return len(segments) <= 2 and any(
        segment
        in {
            "about",
            "company",
            "contact",
            "contacts",
            "kontakty",
            "o-kompanii",
            "o-nas",
            "profile",
        }
        for segment in segments
    )


def _hard_blocked(text: str, host: str) -> bool:
    combined = f"{text} {host}"
    return any(term in combined for term in HARD_NEGATIVE_TERMS)


def _generic_portal(text: str, host: str) -> bool:
    if host.endswith(".gov.ru") or host.endswith(".gosuslugi.ru"):
        return True
    if host in BLOCKED_HOSTS:
        return True
    if re.search(r"(?:^|\.)[\w-]+-city\.ru$", host):
        return True
    return any(term in text for term in GENERIC_CONTENT_TERMS)


def _article_like(text: str) -> bool:
    normalized = _normalize(text)
    return bool(ARTICLE_QUESTION_RE.search(normalized)) or any(
        marker in normalized for marker in ARTICLE_MARKERS
    )


def _provider_page_for_local_target(text: str, target: str) -> bool:
    if _online_target(target):
        return False
    normalized = _normalize(text)
    return any(marker in normalized for marker in LOCAL_SERVICE_PAGE_MARKERS)


def _online_target(value: str) -> bool:
    normalized = _normalize(value)
    return any(_phrase_present(normalized, marker) for marker in ONLINE_MARKERS)


def _query_core(primary: str, secondary: str) -> str:
    words: list[str] = []
    audience_aliases = _audience_aliases(primary)
    preferred_audiences = (
        "салон красоты",
        "косметолог",
        "студия",
        "клиника",
        "селлер",
        "бренд",
        "интернет-магазин",
        "маркетинговое агентство",
        "digital-агентство",
        "отдел продаж",
        "агентство недвижимости",
        "риелтор",
        "застройщик",
        "онлайн-школа",
        "эксперт",
        "наставник",
        "консультант",
    )
    normalized_primary = _normalize(primary)
    for preferred in preferred_audiences:
        roots = _important_tokens(preferred)
        if roots and _matches(normalized_primary, roots) == len(roots):
            words.append(preferred)
        if len(words) >= 3:
            break
    if not words and audience_aliases:
        for preferred in preferred_audiences:
            if any(
                preferred.startswith(alias) or alias.startswith(preferred)
                for alias in audience_aliases
            ):
                words.append(preferred)
            if len(words) >= 2:
                break

    if not words:
        words = _important_words(primary)
    seen_roots = {_root(word) for word in words}
    if len(words) < 2:
        for word in _important_words(secondary):
            root = _root(word)
            if root not in seen_roots:
                seen_roots.add(root)
                words.append(word)
    aliases = _category_aliases(f"{primary} {secondary}")
    for preferred in (
        "селлер", "маркетплейс", "wildberries", "ozon", "автоматизация",
        "нейросети", "дизайн", "инфографика", "косметолог", "салон",
        "недвижимость", "юридические", "онлайн-школа",
    ):
        if any(preferred.startswith(alias) or alias.startswith(preferred) for alias in aliases):
            root = _root(preferred)
            if root not in seen_roots:
                seen_roots.add(root)
                words.append(preferred)
    return " ".join(words[:9]) or _normalize(primary or secondary)


def _query_plans(primary: str, secondary: str, region: str) -> list[tuple[str, str]]:
    core = _query_core(primary, secondary)
    region = _normalize(region)
    negatives = (
        "-казино -ставки -букмекер -вакансии -новости -погода "
        "-статья -блог -инструкция -рейтинг -топ -вебинар -анкета"
    )
    combined = f"{primary} {secondary}"
    if _online_target(combined):
        return [
            ("google", f"{core} {region} официальный сайт контакты {negatives}".strip()),
            ("google", f"{core} {region} компания бренд магазин услуги {negatives}".strip()),
        ]
    return [
        ("google_maps", f"{core} {region}".strip()),
        ("google", f"{core} {region} официальный сайт телефон {negatives}".strip()),
    ]


def _extract_phone(text: str) -> str:
    match = PHONE_RE.search(text)
    return match.group(0).strip() if match else ""


def _parse_payload(payload: dict[str, Any], query: str, engine: str) -> list[Lead]:
    leads: list[Lead] = []
    if engine == "google_maps":
        for item in payload.get("local_results", []) or []:
            if not isinstance(item, dict):
                continue
            if any(
                item.get(marker) is True
                or str(item.get(marker) or "").strip().lower()
                in {"1", "true", "yes"}
                for marker in ("sponsored", "ad", "is_ad")
            ):
                continue
            state = _normalize(
                " ".join(
                    str(item.get(field) or "")
                    for field in ("open_state", "hours", "status")
                )
            )
            if any(
                marker in state
                for marker in (
                    "permanently closed",
                    "temporarily closed",
                    "closed permanently",
                    "закрыто навсегда",
                    "временно закрыто",
                )
            ):
                continue
            name = str(item.get("title") or "").strip()
            if not name:
                continue
            website = str(item.get("website") or "").strip()
            if not website.startswith(("https://", "http://")):
                website = ""
            phone = str(item.get("phone") or "").strip()
            address = str(item.get("address") or "").strip()
            place_id = str(item.get("place_id") or item.get("data_id") or "").strip()
            has_place_identity = any(
                (
                    place_id,
                    item.get("data_cid"),
                    item.get("gps_coordinates"),
                    address,
                )
            )
            if not has_place_identity or not (website or phone):
                continue
            maps_params = {
                "api": "1",
                "query": ", ".join(
                    part for part in (name, address) if part
                ),
            }
            if place_id.startswith("ChI"):
                maps_params["query_place_id"] = place_id
            source_url = (
                "https://www.google.com/maps/search/?"
                + urlencode(maps_params)
            )
            snippet_parts = [
                str(item.get("type") or "").strip(),
                str(item.get("description") or "").strip()[:240],
                f"рейтинг {item['rating']}" if item.get("rating") else "",
                f"отзывов {item['reviews']}" if item.get("reviews") else "",
            ]
            leads.append(
                Lead(
                    name=name,
                    source_url=source_url,
                    website=website,
                    phone=phone,
                    address=address,
                    snippet=", ".join(part for part in snippet_parts if part),
                    query=query,
                )
            )
        return leads

    for item in payload.get("organic_results", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("title") or "").strip()
        link = str(item.get("link") or "").strip()
        if not name or not link.startswith(("https://", "http://")):
            continue
        snippet = str(item.get("snippet") or "").strip()
        leads.append(
            Lead(
                name=name,
                source_url=link,
                website=link,
                phone=_extract_phone(f"{name} {snippet}"),
                snippet=snippet,
                query=query,
            )
        )
    return leads


def _quality(
    lead: Lead,
    primary: str,
    secondary: str,
    region: str,
    engine: str,
    priorities: str = "",
) -> int | None:
    title = _normalize(lead.name)
    body = _normalize(f"{lead.snippet} {lead.address}")
    host = _host(lead.website or lead.source_url)
    full = _normalize(f"{title} {body} {host}")

    if _hard_blocked(full, host):
        return None
    if engine == "google":
        page_url = lead.website or lead.source_url
        if (
            _generic_portal(full, host)
            or _article_like(title)
            or _blocked_content_path(page_url)
            or not _business_surface(page_url)
            or _provider_page_for_local_target(
                f"{title} {body}",
                f"{primary} {secondary}",
            )
        ):
            return None

    primary_roots = _important_tokens(primary)
    secondary_roots = _important_tokens(secondary)
    primary_title = _matches(title, primary_roots)
    primary_body = _matches(body, primary_roots)
    secondary_title = _matches(title, secondary_roots)
    secondary_body = _matches(body, secondary_roots)

    primary_aliases = _category_aliases(primary)
    secondary_aliases = _category_aliases(secondary)
    audience_aliases = _audience_aliases(primary)
    primary_alias_hits = _alias_hits(full, primary_aliases)
    secondary_alias_hits = _alias_hits(full, secondary_aliases)
    audience_hits = _alias_hits(full, audience_aliases)

    if audience_aliases and audience_hits == 0:
        return None

    primary_signal = primary_title * 3 + primary_body * 2 + primary_alias_hits * 2
    secondary_signal = secondary_title * 2 + secondary_body + secondary_alias_hits

    if primary_roots or primary_aliases:
        relevant = primary_signal >= 2
    else:
        relevant = secondary_signal >= 2
    if not relevant:
        return None

    region_roots = _important_tokens(region)
    region_hit = _matches(full, region_roots) > 0 if region_roots else False
    online = _online_target(f"{primary} {secondary}")
    if engine == "google" and not online and region_roots and not region_hit:
        return None

    score = 35
    score += min(primary_title * 12, 36)
    score += min(primary_body * 6, 18)
    score += min(primary_alias_hits * 7, 21)
    score += min(secondary_signal * 2, 12)
    score += 8 if region_hit else 0
    score += 8 if lead.phone else 0
    score += 5 if lead.address else 0
    score += 5 if lead.website else 0
    priority_hits = _matches(full, _important_tokens(priorities))
    score += min(priority_hits * 3, 9)
    return max(1, min(score, 100))


def _dedupe_key(lead: Lead) -> str:
    name = _normalize(lead.name)
    address = _normalize(lead.address)
    if name and address:
        return f"place:{name}|{address}"
    phone = re.sub(r"\D+", "", lead.phone)
    if phone:
        return f"phone:{phone}"
    url = lead.website or lead.source_url
    host = _host(url)
    if host in SOCIAL_PROFILE_HOSTS or host in PROFILE_PAGE_HOSTS:
        return f"profile:{host}/{'/'.join(_path_segments(url))}"
    if host:
        return host
    return _normalize(f"{lead.name}|{lead.source_url}")


def install_search_quality(bot_class: type[Any], serpapi_class: type[Any]) -> None:
    """Install strict lead relevance checks and client-focused search queries."""
    if getattr(serpapi_class, "_quality_search_installed", False):
        return

    original_search = serpapi_class.search

    @wraps(original_search)
    def search(self: Any, target: str, region: str, limit: int = 5) -> list[Lead]:
        limit = max(1, min(int(limit), 20))
        primary, secondary, priorities = _split_target(target)
        visible_query = " ".join(part for part in (primary, secondary, region) if part).strip()

        if self.demo_mode:
            return self._demo(visible_query)[:limit]

        candidate_limit = min(max(limit * 4, 12), 40)
        accepted: dict[str, Lead] = {}

        for engine, query in _query_plans(primary, secondary, region):
            params: dict[str, object] = {
                "engine": engine,
                "q": query,
                "hl": "ru",
                "api_key": self.api_key,
            }
            if engine == "google":
                params.update({"gl": "ru", "num": candidate_limit})
            else:
                params["type"] = "search"

            request = Request(
                f"{self.endpoint}?{urlencode(params)}",
                headers={"User-Agent": "LeadPilot/1.1"},
            )
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))

            error = str(payload.get("error") or "").strip()
            if error:
                normalized_error = error.casefold()
                if (
                    "hasn't returned any results" in normalized_error
                    or "no results" in normalized_error
                ):
                    continue
                raise RuntimeError(error)

            for lead in _parse_payload(payload, visible_query, engine):
                quality = _quality(
                    lead,
                    primary,
                    secondary,
                    region,
                    engine,
                    priorities,
                )
                if quality is None:
                    continue
                lead.score = quality
                key = _dedupe_key(lead)
                current = accepted.get(key)
                if current is None or lead.score > current.score:
                    accepted[key] = lead

            if len(accepted) >= limit:
                break

        return sorted(
            accepted.values(),
            key=lambda item: (item.score, bool(item.phone), bool(item.website)),
            reverse=True,
        )[:limit]

    async def select_search_limit(
        self: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> int:
        from .bot import MENU

        query = update.callback_query
        if not query or not query.from_user:
            return ConversationHandler.END
        await query.answer()
        if query.data == "search_limit:cancel":
            context.user_data.clear()
            await query.message.reply_text("Поиск отменён.", reply_markup=MENU)
            return ConversationHandler.END
        try:
            limit = int((query.data or "").partition(":")[2])
        except ValueError:
            await query.message.reply_text(
                "Не удалось определить количество. Запустите поиск ещё раз.",
                reply_markup=MENU,
            )
            return ConversationHandler.END

        project = dict(context.user_data["search_project"])
        segment = str(context.user_data["search_segment"]).strip()
        region = str(context.user_data["search_region"]).strip()
        project_niche = str(project.get("niche") or "").strip()

        target = segment
        if (
            project_niche
            and _normalize(project_niche) not in _normalize(segment)
            and _normalize(segment) not in _normalize(project_niche)
        ):
            target = f"{segment}{TARGET_SEPARATOR}{project_niche}"

        await query.message.reply_text(
            f"Ищу для проекта «{project['name']}»: {segment}, {region}. "
            "Проверяю релевантность каждого результата…"
        )
        await self._search_and_reply(
            update,
            target,
            region,
            limit,
            project_id=int(project["id"]),
        )
        context.user_data.clear()
        return ConversationHandler.END

    current_search_and_reply = bot_class._search_and_reply

    @wraps(current_search_and_reply)
    async def search_and_reply(
        self: Any,
        update: Update,
        target: str,
        region: str,
        limit: int,
        *,
        project_id: int | None = None,
    ) -> list[Lead]:
        user = update.effective_user
        before = (
            await asyncio.to_thread(self.db.get_usage_snapshot, user.id)
            if user and hasattr(self.db, "get_usage_snapshot")
            else {}
        )
        result = await current_search_and_reply(
            self,
            update,
            target,
            region,
            limit,
            project_id=project_id,
        )
        if not result and user and hasattr(self.db, "get_usage_snapshot"):
            after = await asyncio.to_thread(self.db.get_usage_snapshot, user.id)
            before_used = int(before.get("used", {}).get("searches", 0))
            after_used = int(after.get("used", {}).get("searches", 0))
            if after_used > before_used:
                await asyncio.to_thread(self.db.refund_usage, user.id, "searches", 1)
        return result

    serpapi_class.search = search
    serpapi_class._quality_search_installed = True
    bot_class.select_search_limit = select_search_limit
    bot_class._search_and_reply = search_and_reply
    bot_class._search_quality_installed = True
