from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

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
    "pinterest.com",
    "pin.it",
    "tgstat.ru",
    "tgstat.com",
    "telemetr.io",
    "telemetr.me",
    "livejournal.com",
    "livejournal.ru",
}

VERIFICATION_BLOCKED_HOSTS = {
    "facebook.com",
    "fb.com",
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
    r"catalog|каталог|articles?|статьи?|blog|blogs|блог|news|новости|"
    r"posts?|посты?|tags?|теги?|search|поиск|topics?|темы?|"
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

LARGE_BRAND_RE = re.compile(
    r"\b(?:медси|medsi|см[-\s]?клиник\w*|см[-\s]?косметолог\w*|"
    r"будь\s+здоров|мать\s+и\s+дитя|семейн\w*\s+доктор|"
    r"европейск\w*\s+медицинск\w*\s+центр)\b",
    re.IGNORECASE,
)

LARGE_COUNT_RE = re.compile(
    r"\b(?:более\s+|свыше\s+|около\s+)?(\d{2,})\s+"
    r"(?:врач\w*|специалист\w*|косметолог\w*|сотрудник\w*)\b|"
    r"\b(?:более\s+|свыше\s+|около\s+)?([3-9]|\d{2,})\s+"
    r"(?:клиник\w*|филиал\w*|отделени\w*|салон\w*|центр\w*)\b",
    re.IGNORECASE,
)

ENTERPRISE_PRIMARY_RE = re.compile(
    r"\b(?:медицинск\w*\s+холдинг|группа\s+компани\w*|"
    r"сеть\s+медицинск\w*\s+центр\w*|сеть\s+клиник\w*|"
    r"многопрофильн\w*\s+(?:клиник\w*|центр\w*)|"
    r"крупн\w*\s+(?:клиник\w*|сеть|холдинг)|"
    r"единый\s+контактн\w*\s+центр)\b",
    re.IGNORECASE,
)

ENTERPRISE_SUPPORT_RE = re.compile(
    r"\b(?:личн\w*\s+кабинет|мобильн\w*\s+приложени\w*|"
    r"собственн\w*\s+приложени\w*|колл[-\s]?центр|"
    r"работаем\s+в\s+нескольк\w*\s+регион\w*|"
    r"представлен\w*\s+в\s+\d+\s+город\w*)\b",
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

ACCESS_BLOCKED_RE = re.compile(
    r"(?:log\s+in\s+or\s+sign\s+up\s+to\s+view|"
    r"log\s+in\s+to\s+continue|sign\s+up\s+to\s+see|"
    r"войдите,?\s+чтобы\s+(?:продолжить|посмотреть)|"
    r"требуется\s+авторизац|контент\s+недоступен\s+без\s+входа)",
    re.IGNORECASE,
)

COSMETOLOGY_CORE_SERVICE_RE = re.compile(
    r"(?:инъекц\w*|контурн\w*\s+пластик\w*|ботокс|ботулин\w*|"
    r"филлер\w*|биоревитал\w*|мезотерап\w*|пилинг\w*|"
    r"чистк\w*\s+(?:лица|лиц)|уход\w*\s+за\s+лиц\w*|"
    r"аппаратн\w*\s+косметолог\w*|лазерн\w*\s+(?:омолож|косметолог)\w*|"
    r"дерматолог\w*|косметологическ\w*\s+процедур\w*)",
    re.IGNORECASE,
)

ADJACENT_BEAUTY_PRIMARY_RE = re.compile(
    r"(?:наращиван\w*\s+ресниц\w*|ламинирован\w*\s+ресниц\w*|"
    r"л[эе]шмейкер\w*|lash(?:maker|master)?|"
    r"визажист\w*|макияж\w*|makeup|make-up|мейкап|"
    r"бровист\w*|оформлен\w*\s+бров\w*|brow(?:master)?|"
    r"маникюр\w*|педикюр\w*|nail(?:master)?|ногтев\w*|"
    r"парикмахер\w*|стрижк\w*|окрашиван\w*\s+волос\w*|hair(?:master)?|"
    r"перманентн\w*\s+макияж\w*|татуаж\w*)",
    re.IGNORECASE,
)

GENERIC_LISTING_TITLE_RE = re.compile(
    r"^\s*(?:"
    r"специалисты?\s+косметологи?|"
    r"врачи?[-\s]?косметологи?|"
    r"косметологи"
    r")\b.*\b(?:в\s+[а-яё-]+|цены?|запись|прием|контакты?)\b",
    re.IGNORECASE,
)

SEO_GENERIC_TITLE_RE = re.compile(
    r"^\s*(?:"
    r"косметолог(?:ия|ический\s+кабинет)?|"
    r"центр\s+(?:эстетической\s+)?косметологии|"
    r"услуги\s+косметолога|врач[-\s]?косметолог|"
    r"специалисты?\s+косметологи?"
    r")\b.*\b(?:"
    r"в\s+[а-яё-]+|контакты?|цены?|прайс|запись|онлайн|"
    r"для\s+(?:мужчин|женщин)|адрес|рядом|прием|консультация"
    r")\b",
    re.IGNORECASE,
)

ONLINE_BOOKING_RE = re.compile(
    r"(?:онлайн[-\s]?запис|запись\s+онлайн|записаться\s+онлайн|"
    r"записаться\s+на\s+(?:при[её]м|процедур)|"
    r"yclients|dikidi|altegio|alteg\.io|bookform|"
    r"виджет\s+записи|личн\w*\s+кабинет|мобильн\w*\s+приложени\w*)",
    re.IGNORECASE,
)

BOOKING_PAIN_RE = re.compile(
    r"(?:нет\s+онлайн[-\s]?запис|без\s+онлайн[-\s]?запис|"
    r"ручн\w*\s+запис|личн\w*\s+сообщ|директ|direct|"
    r"комментари|запись\s+через\s+сообщ|через\s+(?:whatsapp|ватсап|telegram|телеграм|vk|вк))",
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

ROLE_RE = re.compile(
    r"(?:врач[-\s]?)?(?:косметолог|эстетист|бровист|визажист|"
    r"л[эе]шмейкер|lash[-\s]?мастер|мастер\s+маникюра|"
    r"массажист|парикмахер|стилист)",
    re.IGNORECASE,
)

PERSON_AFTER_ROLE_RE = re.compile(
    rf"{ROLE_RE.pattern}\s+([А-ЯЁA-Z][а-яёa-z]{{2,}}(?:\s+[А-ЯЁA-Z][а-яёa-z]{{2,}}){{0,2}})",
    re.IGNORECASE,
)

PERSON_BEFORE_ROLE_RE = re.compile(
    rf"([А-ЯЁA-Z][а-яёa-z]{{2,}}(?:\s+[А-ЯЁA-Z][а-яёa-z]{{2,}}){{0,2}})\s*[-—|:]\s*{ROLE_RE.pattern}",
    re.IGNORECASE,
)

QUOTED_BRAND_RE = re.compile(r"[«\"']([^«»\"']{3,60})[»\"']")
HANDLE_RE = re.compile(r"@([a-z0-9_.-]{3,40})", re.IGNORECASE)

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

GENERIC_TOKEN_PREFIXES = (
    "косметолог",
    "косметологичес",
    "эстетичес",
    "контурн",
    "инъекцион",
    "аппаратн",
    "лазерн",
    "пластик",
    "процедур",
    "услуг",
    "специалист",
    "врач",
    "центр",
    "клиник",
    "салон",
    "студи",
    "кабинет",
    "цен",
    "прайс",
    "запис",
    "прием",
    "консультац",
    "взросл",
    "детск",
    "медицинск",
    "частн",
    "луч",
    "топ",
)

GENERIC_STOPWORDS = {
    "в",
    "на",
    "и",
    "по",
    "для",
    "из",
    "с",
    "к",
    "от",
    "до",
    "москва",
    "москве",
    "москвы",
    "московский",
    "россия",
}


@dataclass(frozen=True)
class QualityDecision:
    accepted: bool
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class IdentityDecision:
    accepted: bool
    score: int
    name: str
    reason: str
    generic: bool = False
    max_score: int = 100


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


def is_social_post_url(url: str | None) -> bool:
    if not url:
        return False

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    parts = [part for part in parsed.path.split("/") if part]

    if host == "vk.com" and parts:
        return bool(re.match(r"(?:wall|photo|video|clip)-?\d+_\d+", parts[0]))

    if host in {"t.me", "telegram.me"}:
        if parts and parts[0] == "s":
            parts = parts[1:]
        return len(parts) >= 2 and parts[-1].isdigit()

    if host == "instagram.com" and parts:
        return parts[0].lower() in {"p", "reel", "reels", "stories", "tv"}

    if host == "tiktok.com":
        return "video" in parts

    if host in {"youtube.com", "youtu.be"}:
        return host == "youtu.be" or (parts and parts[0] in {"watch", "shorts"})

    return False


def canonical_social_profile_url(url: str | None, text: str = "") -> str | None:
    if not url:
        return None

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    parts = [part for part in parsed.path.split("/") if part]

    if host == "vk.com":
        if not parts:
            return None

        wall_match = re.match(r"wall(-?)(\d+)_\d+", parts[0], re.IGNORECASE)
        if wall_match:
            prefix = "club" if wall_match.group(1) == "-" else "id"
            return f"https://vk.com/{prefix}{wall_match.group(2)}"

        if parts[0].lower() in {
            "feed",
            "search",
            "clips",
            "video",
            "photo",
            "market",
            "topic",
        }:
            return None

        return f"https://vk.com/{parts[0]}"

    if host in {"t.me", "telegram.me"}:
        if parts and parts[0] == "s":
            parts = parts[1:]
        if not parts:
            return None

        username = parts[0].lstrip("@")
        if username.startswith("+") or username.lower() in {
            "share",
            "iv",
            "proxy",
            "joinchat",
            "addstickers",
        }:
            return None
        return f"https://t.me/{username}"

    if host == "instagram.com":
        if parts and parts[0].lower() not in {
            "p",
            "reel",
            "reels",
            "stories",
            "tv",
            "explore",
        }:
            return f"https://www.instagram.com/{parts[0].lstrip('@')}/"

        handle_match = HANDLE_RE.search(text or "")
        if handle_match:
            return f"https://www.instagram.com/{handle_match.group(1)}/"
        return None

    if host == "tiktok.com":
        for part in parts:
            if part.startswith("@") and len(part) > 1:
                return f"https://www.tiktok.com/{part}"
        return None

    if host == "youtube.com":
        if parts and parts[0] in {"channel", "c", "user", "@"} and len(parts) >= 2:
            return urlunparse(("https", "www.youtube.com", "/" + "/".join(parts[:2]), "", "", ""))
        if parts and parts[0].startswith("@"):
            return f"https://www.youtube.com/{parts[0]}"
        return None

    return None


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


def _clean_person_candidate(value: str, city: str) -> str | None:
    tokens = re.findall(r"[А-ЯЁA-Z][а-яёa-z]{2,}", value)
    city_values = set(city_terms(city))

    cleaned: list[str] = []
    for token in tokens:
        normalized = normalize_text(token)
        if any(city_value in normalized or normalized in city_value for city_value in city_values):
            break
        if any(normalized.startswith(prefix) for prefix in GENERIC_TOKEN_PREFIXES):
            break
        cleaned.append(token)

    if not cleaned or len(cleaned) > 3:
        return None

    return " ".join(cleaned)


def extract_person_name(title: str, city: str = "") -> str | None:
    for pattern in (PERSON_AFTER_ROLE_RE, PERSON_BEFORE_ROLE_RE):
        match = pattern.search(title or "")
        if not match:
            continue
        candidate = _clean_person_candidate(match.group(1), city)
        if candidate:
            return candidate
    return None


def _distinctive_title_segment(title: str, niche: str, city: str) -> str | None:
    segment = re.split(r"\s+[|—]\s+|\s+-\s+", title or "", maxsplit=1)[0].strip()
    if not segment:
        return None

    normalized = normalize_text(segment)
    niche_values = niche_terms(niche)
    city_values = city_terms(city)
    tokens = re.findall(r"[a-zа-я0-9]+", normalized)
    residue: list[str] = []

    for token in tokens:
        if token in GENERIC_STOPWORDS:
            continue
        if any(token.startswith(prefix) for prefix in GENERIC_TOKEN_PREFIXES):
            continue
        if any(value in token or token in value for value in niche_values if len(value) >= 4):
            continue
        if any(value in token or token in value for value in city_values if len(value) >= 4):
            continue
        residue.append(token)

    if not residue:
        return None

    if len(residue) > 6:
        return None

    return segment[:255]


def _social_handle_from_profile(profile_url: str | None) -> str | None:
    if not profile_url:
        return None
    parsed = urlparse(profile_url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    if parts[0] in {"channel", "c", "user"} and len(parts) > 1:
        return parts[1]
    return parts[0].lstrip("@")


def assess_identity(
    *,
    title: str,
    snippet: str,
    url: str,
    niche: str,
    city: str,
) -> IdentityDecision:
    person_name = extract_person_name(title, city)
    if person_name:
        return IdentityDecision(
            True, 25, person_name, "найдено имя специалиста", False, 100
        )

    quoted = QUOTED_BRAND_RE.search(title or "")
    if quoted:
        quoted_name = quoted.group(1).strip()
        if _distinctive_title_segment(quoted_name, niche, city):
            return IdentityDecision(
                True, 20, quoted_name, "найдено название бизнеса", False, 100
            )

    profile_url = canonical_social_profile_url(url, f"{title} {snippet}")
    social_post = is_social_post_url(url)
    host = host_of(url)
    title_handle = HANDLE_RE.search(title or "")

    if title_handle:
        return IdentityDecision(
            True,
            18,
            title[:255],
            "найден уникальный публичный профиль",
            False,
            90,
        )

    if SEO_GENERIC_TITLE_RE.search(normalize_text(title)):
        return IdentityDecision(
            True,
            5,
            title[:255],
            "общая SEO-страница без подтвержденного имени",
            True,
            45,
        )

    distinctive = _distinctive_title_segment(title, niche, city)
    if distinctive:
        return IdentityDecision(
            True, 15, distinctive, "найдено различимое название", False, 100
        )

    if social_post:
        return IdentityDecision(
            False,
            0,
            "",
            "социальная публикация не содержит имени специалиста или названия бизнеса",
            True,
            0,
        )

    handle = _social_handle_from_profile(profile_url)
    if handle and host in {"instagram.com", "vk.com", "tiktok.com"}:
        return IdentityDecision(
            True,
            8,
            f"Профиль @{handle}",
            "есть уникальный профиль, но имя бизнеса не подтверждено",
            True,
            60,
        )

    return IdentityDecision(
        False,
        0,
        "",
        "не найдено имя специалиста или различимое название бизнеса",
        True,
        0,
    )


def _custom_exclusion_match(text: str, exclude: str) -> str | None:
    normalized_text = normalize_text(text)

    for raw_item in re.split(r"[,;\n]+", exclude or ""):
        item = normalize_text(raw_item)
        if len(item) >= 4 and item in normalized_text:
            return item

    return None


def verification_rejection_reason(text: str, url: str) -> str | None:
    host = host_of(url)
    normalized = normalize_text(text)

    if is_host_in(host, VERIFICATION_BLOCKED_HOSTS):
        return "страница требует авторизации и не поддается надежной автоматической проверке"

    if ACCESS_BLOCKED_RE.search(normalized):
        return "содержимое страницы недоступно без авторизации"

    return None


def niche_mismatch_reason(
    *,
    title: str,
    snippet: str,
    url: str,
    niche: str,
) -> str | None:
    requested = normalize_text(niche)

    if not any(
        marker in requested
        for marker in ("косметолог", "косметология", "эстетист")
    ):
        return None

    primary_text = normalize_text(f"{title} {url}")
    all_text = normalize_text(f"{title} {snippet} {url}")

    if (
        ADJACENT_BEAUTY_PRIMARY_RE.search(primary_text)
        and not COSMETOLOGY_CORE_SERVICE_RE.search(all_text)
    ):
        return (
            "основная специализация относится к смежной beauty-нише, "
            "а косметологические процедуры не подтверждены"
        )

    return None


def enterprise_rejection_reason(text: str) -> str | None:
    normalized = normalize_text(text)

    if LARGE_BRAND_RE.search(normalized):
        return "известная крупная сеть или медицинский холдинг"

    if CHAIN_RE.search(normalized):
        return "сеть, франшиза или несколько филиалов"

    if ENTERPRISE_PRIMARY_RE.search(normalized):
        return "крупная клиника, холдинг или группа компаний"

    if LARGE_COUNT_RE.search(normalized):
        return "слишком много клиник, филиалов, врачей или специалистов"

    support_signals = ENTERPRISE_SUPPORT_RE.findall(normalized)
    if len(support_signals) >= 2:
        return "набор признаков крупной организации"

    return None


def _positive_online_booking(text: str) -> bool:
    normalized = normalize_text(text)

    for match in ONLINE_BOOKING_RE.finditer(normalized):
        prefix = normalized[max(0, match.start() - 48):match.start()]
        suffix = normalized[match.end():match.end() + 36]

        negated_before = re.search(
            r"(?:нет|без|не\s+найден\w*|не\s+обнаружен\w*|"
            r"отсутств\w*)(?:\s+[a-zа-я0-9-]+){0,4}\s*$",
            prefix,
            re.IGNORECASE,
        )
        negated_after = re.search(
            r"(?:не\s+найден\w*|не\s+обнаружен\w*|отсутств\w*)",
            suffix,
            re.IGNORECASE,
        )

        if negated_before or negated_after:
            continue

        return True

    return False


def target_pain_contradiction_reason(
    text: str,
    target_pain: str = "",
) -> str | None:
    if not target_pain or not BOOKING_PAIN_RE.search(target_pain):
        return None

    if _positive_online_booking(text):
        return "у лида уже есть онлайн-запись или готовая система бронирования"

    return None


def hard_rejection_reason(text: str, url: str, exclude: str = "") -> str | None:
    normalized = normalize_text(text)

    verification = verification_rejection_reason(normalized, url)
    if verification:
        return verification

    if hard_bad_host(url):
        return "каталог, агрегатор, отзывы, вакансии или информационный сайт"

    if hard_bad_path(url):
        return "служебная, обзорная, каталожная или вакансионная страница"

    enterprise = enterprise_rejection_reason(normalized)
    if enterprise:
        return enterprise

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
    target_pain: str = "",
) -> QualityDecision:
    combined = " ".join(part for part in (title, snippet, url) if part)
    normalized = normalize_text(combined)
    rejection = hard_rejection_reason(normalized, url, exclude)

    if rejection:
        return QualityDecision(False, 0, (rejection,))

    contradiction = target_pain_contradiction_reason(normalized, target_pain)
    if contradiction:
        return QualityDecision(False, 0, (contradiction,))

    if not text_matches_niche(normalized, niche):
        return QualityDecision(False, 0, ("не подтверждена запрошенная ниша",))

    mismatch = niche_mismatch_reason(
        title=title,
        snippet=snippet,
        url=url,
        niche=niche,
    )
    if mismatch:
        return QualityDecision(False, 0, (mismatch,))

    if GENERIC_LISTING_TITLE_RE.search(normalize_text(title)):
        return QualityDecision(
            False,
            0,
            ("общая страница списка специалистов без конкретного лида",),
        )

    identity = assess_identity(
        title=title,
        snippet=snippet,
        url=url,
        niche=niche,
        city=city,
    )
    if not identity.accepted:
        return QualityDecision(False, 0, (identity.reason,))

    host = host_of(url)
    city_match = text_matches_city(normalized, city)
    local_signal = bool(SMALL_BUSINESS_RE.search(normalized))
    action_signal = bool(CONTACT_OR_ACTION_RE.search(normalized))
    profile_url = canonical_social_profile_url(url, combined)
    social_profile = bool(profile_url and is_social_host(host))

    if require_city and not city_match:
        return QualityDecision(False, 0, ("не подтвержден запрошенный город",))

    score = 25
    reasons = ["подтверждена ниша +25"]

    score += identity.score
    reasons.append(f"{identity.reason} +{identity.score}")

    if city_match:
        score += 15
        reasons.append("подтвержден город +15")
    else:
        score -= 10
        reasons.append("город не подтвержден -10")

    if local_signal:
        score += 15
        reasons.append("есть признаки локального бизнеса +15")

    if action_signal:
        score += 10
        reasons.append("есть запись или прямой контакт +10")

    if social_profile:
        score += 5
        reasons.append("найден канонический профиль бизнеса +5")

    if score > identity.max_score:
        score = identity.max_score
        reasons.append(
            f"потолок {identity.max_score}: {identity.reason}"
        )

    accepted = score >= 45 and (local_signal or action_signal or social_profile)

    if not accepted:
        reasons.append("недостаточно признаков реального локального бизнеса")

    return QualityDecision(
        accepted,
        max(0, min(100, score)),
        tuple(reasons),
    )


def canonical_result_key(url: str, text: str = "") -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")

    if is_social_host(host):
        profile_url = canonical_social_profile_url(url, text)
        if not profile_url:
            return ""
        profile = urlparse(profile_url)
        profile_host = (profile.hostname or "").lower().removeprefix("www.")
        profile_path = re.sub(r"/+", "/", profile.path or "/").rstrip("/").lower()
        return f"{profile_host}{profile_path}"

    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/").lower()
    if is_booking_host(host):
        return f"{host}{path}"
    return host


def rank_search_results(results, req: SearchRequest, max_results: int):
    ranked = []
    seen_keys: set[str] = set()

    for result in results:
        combined = f"{result.title} {result.snippet}"
        key = canonical_result_key(result.url, combined)
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
            target_pain=req.target_pain,
        )

        if not decision.accepted:
            continue

        seen_keys.add(key)
        result.profile_url = canonical_social_profile_url(result.url, combined)
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


def pain_is_explicit(value: str | None) -> bool:
    normalized = normalize_text(value)
    if not pain_is_confirmed(value):
        return False
    inferred_markers = (
        "не обнаружен",
        "не обнаружены",
        "на проверенной странице не найдена",
        "в поиске найден только профиль",
        "отдельный сайт не обнаружен",
    )
    return not any(marker in normalized for marker in inferred_markers)


def offer_is_relevant(value: str | None) -> bool:
    normalized = normalize_text(value)
    return bool(
        normalized
        and not normalized.startswith("не предлагать автоматически")
    )
