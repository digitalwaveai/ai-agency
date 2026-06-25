from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.schemas import LeadCreate


EMAIL_RE = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
    re.IGNORECASE,
)

PHONE_RE = re.compile(
    r"(?:\+?\d[\d\s().-]{7,}\d)"
)

MAX_PAGE_BYTES = 1_000_000
MAX_CONTACT_PAGES = 2

SOCIAL_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "vk.com",
    "www.vk.com",
    "t.me",
    "telegram.me",
    "wa.me",
    "api.whatsapp.com",
    "whatsapp.com",
    "www.whatsapp.com",
}

CONTACT_HINTS = (
    "contact",
    "contacts",
    "kontakt",
    "kontakty",
    "контакт",
    "контакты",
    "связаться",
    "about",
    "о нас",
)


@dataclass
class ExtractedContacts:
    email: str | None = None
    phone: str | None = None
    telegram_url: str | None = None
    instagram_url: str | None = None
    vk_url: str | None = None
    whatsapp: str | None = None
    contact_pages: list[str] = field(default_factory=list)

    def merge(self, other: "ExtractedContacts") -> None:
        fields = (
            "email",
            "phone",
            "telegram_url",
            "instagram_url",
            "vk_url",
            "whatsapp",
        )

        for field_name in fields:
            if not getattr(self, field_name):
                value = getattr(other, field_name)

                if value:
                    setattr(self, field_name, value)

        for url in other.contact_pages:
            if url not in self.contact_pages:
                self.contact_pages.append(url)


def _is_missing(value: str | None) -> bool:
    return (
        not value
        or value.strip().lower() == "не найден"
    )


def _host(url: str | None) -> str:
    if not url:
        return ""

    return (urlparse(url).hostname or "").lower()


def _skip_host(host: str) -> bool:
    if not host:
        return True

    if host in {"example.com", "www.example.com"}:
        return True

    if host.endswith((".test", ".invalid", ".localhost")):
        return True

    return any(
        host == social_host
        or host.endswith("." + social_host)
        for social_host in SOCIAL_HOSTS
    )


def _clean_email(value: str) -> str | None:
    email = (
        unquote(value)
        .strip()
        .strip(".,;:()[]<>")
    )

    if EMAIL_RE.fullmatch(email):
        return email

    return None


def _clean_phone(value: str) -> str | None:
    phone = unquote(value).strip()
    digits = re.sub(r"\D", "", phone)

    if 10 <= len(digits) <= 15:
        return phone

    return None


def _social_fields_from_url(
    url: str | None,
) -> dict[str, str]:
    if not url:
        return {}

    host = _host(url)

    if host.endswith("instagram.com"):
        return {"instagram_url": url}

    if host in {"t.me", "telegram.me"}:
        return {"telegram_url": url}

    if host.endswith("vk.com"):
        return {"vk_url": url}

    if host in {
        "wa.me",
        "api.whatsapp.com",
        "whatsapp.com",
        "www.whatsapp.com",
    }:
        return {"whatsapp": url}

    return {}


async def _is_safe_public_url(url: str) -> bool:
    parsed = urlparse(url)

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
    ):
        return False

    port = parsed.port or (
        443 if parsed.scheme == "https" else 80
    )

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


async def _fetch_html(
    client: httpx.AsyncClient,
    url: str,
) -> tuple[str, str] | None:
    current_url = url

    for _ in range(4):
        if not await _is_safe_public_url(current_url):
            return None

        try:
            async with client.stream(
                "GET",
                current_url,
                follow_redirects=False,
            ) as response:
                if response.status_code in {
                    301,
                    302,
                    303,
                    307,
                    308,
                }:
                    location = response.headers.get(
                        "location"
                    )

                    if not location:
                        return None

                    current_url = urljoin(
                        current_url,
                        location,
                    )
                    continue

                if response.status_code >= 400:
                    return None

                content_type = response.headers.get(
                    "content-type",
                    "",
                ).lower()

                if (
                    "text/html" not in content_type
                    and "application/xhtml+xml"
                    not in content_type
                ):
                    return None

                body = bytearray()

                async for chunk in response.aiter_bytes():
                    body.extend(chunk)

                    if len(body) >= MAX_PAGE_BYTES:
                        break

                encoding = response.encoding or "utf-8"

                html = bytes(
                    body[:MAX_PAGE_BYTES]
                ).decode(
                    encoding,
                    errors="replace",
                )

                return html, str(response.url)

        except httpx.HTTPError:
            return None

    return None


def extract_contacts_from_html(
    html: str,
    base_url: str,
) -> ExtractedContacts:
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    for tag in soup(
        ["script", "style", "noscript", "template"]
    ):
        tag.decompose()

    result = ExtractedContacts()
    text = soup.get_text(" ", strip=True)

    email_match = EMAIL_RE.search(text)

    if email_match:
        result.email = _clean_email(
            email_match.group(0)
        )

    phone_match = PHONE_RE.search(text)

    if phone_match:
        result.phone = _clean_phone(
            phone_match.group(0)
        )

    base_host = _host(base_url)

    for link in soup.find_all("a", href=True):
        raw_href = str(
            link.get("href") or ""
        ).strip()

        if not raw_href:
            continue

        lowered = raw_href.lower()

        if lowered.startswith("mailto:"):
            value = (
                raw_href.split(":", 1)[1]
                .split("?", 1)[0]
            )

            email = _clean_email(value)

            if email and not result.email:
                result.email = email

            continue

        if lowered.startswith("tel:"):
            value = (
                raw_href.split(":", 1)[1]
                .split("?", 1)[0]
            )

            phone = _clean_phone(value)

            if phone and not result.phone:
                result.phone = phone

            continue

        absolute_url = urljoin(
            base_url,
            raw_href,
        )

        host = _host(absolute_url)

        if (
            host in {"t.me", "telegram.me"}
            and not result.telegram_url
        ):
            result.telegram_url = absolute_url

        elif (
            host.endswith("instagram.com")
            and not result.instagram_url
        ):
            result.instagram_url = absolute_url

        elif (
            host.endswith("vk.com")
            and not result.vk_url
        ):
            result.vk_url = absolute_url

        elif (
            host in {
                "wa.me",
                "api.whatsapp.com",
                "whatsapp.com",
                "www.whatsapp.com",
            }
            and not result.whatsapp
        ):
            result.whatsapp = absolute_url

        anchor_text = link.get_text(
            " ",
            strip=True,
        ).lower()

        hint_text = (
            lowered + " " + anchor_text
        )

        if (
            host == base_host
            and any(
                hint in hint_text
                for hint in CONTACT_HINTS
            )
            and absolute_url
            not in result.contact_pages
        ):
            result.contact_pages.append(
                absolute_url
            )

    return result


async def _enrich_one_lead(
    lead: LeadCreate,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> LeadCreate:
    updates: dict[str, str] = {}

    for candidate_url in (
        lead.website_url,
        lead.source_url,
    ):
        social_fields = _social_fields_from_url(
            candidate_url
        )

        for field_name, value in social_fields.items():
            current = getattr(
                lead,
                field_name,
                None,
            )

            if _is_missing(current):
                updates[field_name] = value

    website_url = lead.website_url

    if not website_url:
        return lead.model_copy(
            update=updates
        )

    host = _host(website_url)

    if _skip_host(host):
        return lead.model_copy(
            update=updates
        )

    async with semaphore:
        fetched = await _fetch_html(
            client,
            website_url,
        )

        if not fetched:
            return lead.model_copy(
                update=updates
            )

        html, final_url = fetched

        extracted = extract_contacts_from_html(
            html,
            final_url,
        )

        for contact_url in (
            extracted.contact_pages[
                :MAX_CONTACT_PAGES
            ]
        ):
            contact_page = await _fetch_html(
                client,
                contact_url,
            )

            if not contact_page:
                continue

            contact_html, contact_final_url = (
                contact_page
            )

            extracted.merge(
                extract_contacts_from_html(
                    contact_html,
                    contact_final_url,
                )
            )

    for field_name in (
        "email",
        "phone",
        "telegram_url",
        "instagram_url",
        "vk_url",
        "whatsapp",
    ):
        current = getattr(
            lead,
            field_name,
            None,
        )

        extracted_value = getattr(
            extracted,
            field_name,
        )

        if (
            _is_missing(current)
            and extracted_value
        ):
            updates[field_name] = extracted_value

    return lead.model_copy(
        update=updates
    )


async def enrich_leads_from_web(
    leads: list[LeadCreate],
    concurrency: int = 3,
) -> list[LeadCreate]:
    if not leads:
        return []

    timeout = httpx.Timeout(
        10.0,
        connect=5.0,
    )

    limits = httpx.Limits(
        max_connections=concurrency,
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; BeautyLeadFinder/1.0)"
        ),
        "Accept": (
            "text/html,"
            "application/xhtml+xml"
        ),
    }

    semaphore = asyncio.Semaphore(
        concurrency
    )

    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        headers=headers,
        trust_env=False,
    ) as client:
        tasks = [
            _enrich_one_lead(
                lead,
                client,
                semaphore,
            )
            for lead in leads
        ]

        results = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

    enriched: list[LeadCreate] = []

    for original, result in zip(
        leads,
        results,
    ):
        if isinstance(result, Exception):
            enriched.append(original)
        else:
            enriched.append(result)

    return enriched
