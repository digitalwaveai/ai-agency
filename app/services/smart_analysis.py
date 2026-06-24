from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from app.schemas import LeadCreate
from app.services.pain_detection import detect_pain
from app.services.search_quality import (
    PLACEHOLDER_HOSTS,
    SOCIAL_HOSTS,
    assess_candidate_text,
    hard_bad_host,
    is_host_in,
    offer_is_relevant,
    pain_is_confirmed,
)


REVIEW_PATH_RE = re.compile(
    r"(?:^|/)(?:reviews?|otzyvy?|отзывы?)(?:/|$)",
    re.IGNORECASE,
)
BOOKING_RE = re.compile(
    r"(онлайн[-\s]?запис|записаться\s+онлайн|yclients|dikidi|altegio|"
    r"bookform|виджет\s+записи|забронировать)",
    re.IGNORECASE,
)
MANUAL_BOOKING_RE = re.compile(
    r"(пишите\s+(?:в\s+)?(?:лс|директ)|личн\w*\s+сообщ\w*|direct|"
    r"запись\s+(?:через|в)\s+(?:whatsapp|ватсап|telegram|телеграм|vk|вк)|"
    r"для\s+записи\s+пишите)",
    re.IGNORECASE,
)
SITE_SERVICE_RE = re.compile(
    r"(?:^|[\s,;/])(сайт|лендинг|landing)(?:$|[\s,;/])",
    re.IGNORECASE,
)
BOOKING_SERVICE_RE = re.compile(
    r"(онлайн[-\s]?запис|автоматизац\w*\s+запис|форма\s+записи|виджет\s+записи)",
    re.IGNORECASE,
)
BOT_SERVICE_RE = re.compile(
    r"(бот|chatbot|чат[-\s]?бот|telegram[-\s]?бот)",
    re.IGNORECASE,
)
SOCIAL_SERVICE_RE = re.compile(
    r"(соцсет|социальн\w*\s+сет|smm|контент)",
    re.IGNORECASE,
)

MAX_PAGE_BYTES = 900_000
MAX_TEXT_CHARS = 12_000


def _host(url: str | None) -> str:
    if not url:
        return ""
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _is_social(url: str | None) -> bool:
    return is_host_in(_host(url), SOCIAL_HOSTS)


def _root_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))


def _canonical_website_url(lead: LeadCreate) -> str | None:
    candidate = lead.website_url or lead.source_url
    host = _host(candidate)

    if (
        not candidate
        or _is_social(candidate)
        or hard_bad_host(candidate)
        or is_host_in(host, PLACEHOLDER_HOSTS)
    ):
        return None

    parsed = urlparse(candidate)
    if REVIEW_PATH_RE.search(parsed.path or ""):
        return _root_url(candidate)

    return candidate


async def _is_public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False

    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            parsed.hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError:
        return False

    if not addresses:
        return False

    for address in addresses:
        raw_ip = address[4][0].split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            return False
        if not ip.is_global:
            return False

    return True


async def _fetch_visible_text(client: httpx.AsyncClient, url: str) -> str:
    current_url = url

    for _ in range(4):
        if not await _is_public_url(current_url):
            return ""

        try:
            async with client.stream("GET", current_url, follow_redirects=False) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        return ""
                    current_url = urljoin(current_url, location)
                    continue

                if response.status_code >= 400:
                    return ""

                content_type = response.headers.get("content-type", "").lower()
                if "html" not in content_type:
                    return ""

                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) >= MAX_PAGE_BYTES:
                        break

                encoding = response.encoding or "utf-8"
                html = bytes(body[:MAX_PAGE_BYTES]).decode(encoding, errors="replace")

        except httpx.HTTPError:
            return ""

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "template", "svg"]):
            tag.decompose()

        return " ".join(soup.get_text(" ", strip=True).split())[:MAX_TEXT_CHARS]

    return ""


def _target_mentions(target_pain: str, words: tuple[str, ...]) -> bool:
    lowered = target_pain.lower()
    return any(word in lowered for word in words)


def _final_pain(
    *,
    analysis_text: str,
    target_pain: str,
    has_site: bool,
    has_booking: bool,
) -> str:
    detected = detect_pain(analysis_text, target_pain)
    if detected != "не найден":
        return detected

    if not has_site and _target_mentions(target_pain, ("сайт", "лендинг")):
        return (
            "Нет отдельного сайта\n"
            "Подтверждение: «В поиске найден только профиль или карточка бизнеса; "
            "отдельный сайт не обнаружен.»"
        )

    if has_site and not has_booking and _target_mentions(
        target_pain,
        ("запис", "брон", "онлайн"),
    ):
        return (
            "На проверенной странице не найдена явная онлайн-запись\n"
            "Подтверждение: «На проанализированной странице не обнаружены "
            "кнопки, ссылки или виджет онлайн-записи.»"
        )

    return (
        "Выбранная боль не подтверждена\n"
        "Подтверждение: «По доступному тексту страницы не найдено достаточно "
        "фактов для уверенного вывода.»"
    )


def _recommend_offer(
    *,
    lead: LeadCreate,
    analysis_text: str,
    requested_services: list[str],
    has_site: bool,
    has_booking: bool,
    manual_booking: bool,
) -> str:
    recommended: list[str] = []

    for service in requested_services:
        value = service.strip()
        if not value:
            continue

        if SITE_SERVICE_RE.search(value):
            if not has_site:
                recommended.append(value)
            continue

        if BOOKING_SERVICE_RE.search(value):
            if not has_booking:
                recommended.append(value)
            continue

        if BOT_SERVICE_RE.search(value):
            if manual_booking:
                recommended.append(value)
            continue

        if SOCIAL_SERVICE_RE.search(value):
            if any(
                _is_social(candidate)
                for candidate in (lead.source_url, lead.instagram_url, lead.vk_url)
            ):
                recommended.append(value)
            continue

        if analysis_text:
            recommended.append(value)

    unique: list[str] = []
    seen: set[str] = set()

    for item in recommended:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)

    if unique:
        return ", ".join(unique)

    return (
        "не предлагать автоматически — "
        "потребность в выбранных услугах не подтверждена"
    )


async def _analyze_one(
    lead: LeadCreate,
    *,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    niche: str,
    city: str,
    target_pain: str,
    services: list[str],
    exclude: str,
    strict_match: bool,
) -> LeadCreate | None:
    website_url = _canonical_website_url(lead)
    page_text = ""

    if website_url and lead.source_type != "demo":
        async with semaphore:
            page_text = await _fetch_visible_text(client, website_url)

    analysis_text = " ".join(
        part for part in (lead.name, lead.description, page_text) if part
    )

    decision = assess_candidate_text(
        title=lead.name,
        snippet=" ".join(part for part in (lead.description, page_text) if part),
        url=lead.source_url,
        niche=niche,
        city=city,
        exclude=exclude,
        require_city=strict_match,
    )

    if not decision.accepted:
        return None

    has_site = bool(website_url)
    has_booking = bool(BOOKING_RE.search(analysis_text))
    manual_booking = bool(MANUAL_BOOKING_RE.search(analysis_text))

    pain_points = _final_pain(
        analysis_text=analysis_text,
        target_pain=target_pain,
        has_site=has_site,
        has_booking=has_booking,
    )

    suggested_offer = _recommend_offer(
        lead=lead,
        analysis_text=analysis_text,
        requested_services=services,
        has_site=has_site,
        has_booking=has_booking,
        manual_booking=manual_booking,
    )

    if strict_match and target_pain and not pain_is_confirmed(pain_points):
        return None

    if strict_match and services and not offer_is_relevant(suggested_offer):
        return None

    updates: dict[str, object] = {
        "pain_points": pain_points,
        "suggested_offer": suggested_offer,
    }

    if website_url:
        updates["website_url"] = website_url

    return lead.model_copy(update=updates)


async def analyze_and_filter_leads(
    leads: list[LeadCreate],
    *,
    niche: str,
    city: str,
    target_pain: str,
    services: list[str],
    exclude: str,
    strict_match: bool = True,
    concurrency: int = 3,
) -> list[LeadCreate]:
    if not leads:
        return []

    timeout = httpx.Timeout(12.0, connect=5.0)
    limits = httpx.Limits(max_connections=concurrency)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; BeautyLeadFinder/2.0)",
        "Accept": "text/html,application/xhtml+xml",
    }
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        headers=headers,
        trust_env=False,
    ) as client:
        tasks = [
            _analyze_one(
                lead,
                client=client,
                semaphore=semaphore,
                niche=niche,
                city=city,
                target_pain=target_pain,
                services=services,
                exclude=exclude,
                strict_match=strict_match,
            )
            for lead in leads
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    analyzed: list[LeadCreate] = []

    for result in results:
        if isinstance(result, Exception):
            continue
        if result is not None:
            analyzed.append(result)

    return analyzed
