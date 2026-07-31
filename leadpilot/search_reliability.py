from __future__ import annotations

import json
import logging
import re
import time
from functools import wraps
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import search_quality as quality
from .models import Lead
from .project_search_context import EXCLUSION_SEPARATOR, _matches_exclusion


JUNK_TITLE_RE = re.compile(
    r"^(?:топ[\s-]?\d*|рейтинг|список|обзор|статья|новости|вакансии|"
    r"как\s|почему\s|зачем\s|сколько\s|анкета|опрос|вебинар|курс\b)",
    re.IGNORECASE,
)

NEGATIVE_QUERY = (
    "-казино -ставки -букмекер -вакансии -резюме -новости -погода "
    "-статья -блог -инструкция -рейтинг -топ -обзор -вебинар -курс "
    "-анкета -опрос -форум"
)


def _trim_query(value: str, limit: int = 360) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _query_plans(
    primary: str,
    secondary: str,
    region: str,
) -> list[tuple[str, str]]:
    core = _trim_query(quality._query_core(primary, secondary), 180)
    region = _trim_query(quality._normalize(region), 80)
    combined = f"{primary} {secondary}"

    if quality._online_target(combined):
        return [
            (
                "google",
                _trim_query(
                    f"{core} {region} официальный сайт контакты компания "
                    f"{NEGATIVE_QUERY}"
                ),
            ),
            (
                "google",
                _trim_query(
                    f"{core} {region} "
                    "(site:vk.com OR site:t.me OR site:taplink.cc "
                    "OR site:instagram.com) "
                    f"{NEGATIVE_QUERY}"
                ),
            ),
        ]

    return [
        ("google_maps", _trim_query(f"{core} {region}", 220)),
        (
            "google",
            _trim_query(
                f"{core} {region} официальный сайт контакты телефон "
                f"{NEGATIVE_QUERY}"
            ),
        ),
    ]


def _decode_http_error(error: HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", errors="replace")
        payload = json.loads(body)
        return str(payload.get("error") or body).strip()
    except Exception:
        return str(error)


def _request_payload(
    client: Any,
    engine: str,
    query: str,
    candidate_limit: int,
) -> dict[str, Any] | None:
    params: dict[str, object] = {
        "engine": engine,
        "q": query,
        "hl": "ru",
        "api_key": client.api_key,
    }
    if engine == "google":
        params.update({"gl": "ru", "num": candidate_limit})
    else:
        params["type"] = "search"

    request = Request(
        f"{client.endpoint}?{urlencode(params)}",
        headers={"User-Agent": "LeadPilot/1.2"},
    )

    for attempt in range(2):
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                logging.warning(
                    "SerpAPI returned a non-object payload for engine=%s",
                    engine,
                )
                return None

            error = str(payload.get("error") or "").strip()
            if error:
                logging.warning(
                    "SerpAPI plan skipped: engine=%s error=%s",
                    engine,
                    error[:240],
                )
                return None
            return payload
        except HTTPError as exc:
            logging.warning(
                "SerpAPI HTTP failure: engine=%s status=%s error=%s",
                engine,
                getattr(exc, "code", "unknown"),
                _decode_http_error(exc)[:240],
            )
            return None
        except (URLError, TimeoutError, OSError) as exc:
            if attempt == 0:
                time.sleep(0.35)
                continue
            logging.warning(
                "SerpAPI network failure: engine=%s error=%s",
                engine,
                str(exc)[:240],
            )
            return None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logging.warning(
                "SerpAPI invalid JSON: engine=%s error=%s",
                engine,
                str(exc)[:240],
            )
            return None
    return None


def _strong_target_match(
    lead: Lead,
    primary: str,
    secondary: str,
    engine: str,
) -> bool:
    title = quality._normalize(lead.name)
    body = quality._normalize(
        f"{lead.snippet} {lead.address} {lead.website}"
    )
    full = quality._normalize(f"{title} {body}")

    if not title or JUNK_TITLE_RE.search(title):
        return False

    if engine == "google_maps":
        return bool(lead.phone or lead.website) and bool(
            lead.address or lead.phone
        )

    page_url = lead.website or lead.source_url
    if not quality._business_surface(page_url):
        return False

    aliases = (
        quality._audience_aliases(primary)
        | quality._category_aliases(primary)
        | quality._category_aliases(secondary)
    )
    if aliases and quality._alias_hits(full, aliases) == 0:
        return False

    roots = quality._important_tokens(primary)
    if not roots:
        roots = quality._important_tokens(secondary)
    if roots:
        title_hits = quality._matches(title, roots)
        body_hits = quality._matches(body, roots)
        required_body_hits = 1 if len(roots) == 1 else 2
        if title_hits == 0 and body_hits < required_body_hits:
            return False

    return True


def _sort_key(lead: Lead) -> tuple[int, bool, bool, bool]:
    return (
        int(lead.score),
        bool(lead.phone),
        bool(lead.address),
        bool(lead.website),
    )


def install_resilient_search(serpapi_class: type[Any]) -> None:
    """Replace fragile multi-plan search with fault-tolerant strict search."""
    if getattr(serpapi_class, "_resilient_search_installed", False):
        return

    previous_search = serpapi_class.search

    @wraps(previous_search)
    def search(
        self: Any,
        target: str,
        region: str,
        limit: int = 5,
    ) -> list[Lead]:
        requested_limit = max(1, min(int(limit), 20))
        search_target, exclusion_separator, exclusions = target.partition(
            EXCLUSION_SEPARATOR
        )
        primary, secondary, priorities = quality._split_target(search_target)
        visible_query = " ".join(
            part for part in (primary, secondary, region) if part
        ).strip()

        if self.demo_mode:
            return self._demo(visible_query)[:requested_limit]

        candidate_limit = min(max(requested_limit * 5, 15), 40)
        accepted: dict[str, Lead] = {}
        attempted = 0
        failed = 0

        for engine, query in _query_plans(primary, secondary, region):
            attempted += 1
            try:
                payload = _request_payload(
                    self,
                    engine,
                    query,
                    candidate_limit,
                )
            except Exception:
                logging.exception(
                    "Unexpected SerpAPI plan failure: engine=%s",
                    engine,
                )
                failed += 1
                continue
            if payload is None:
                failed += 1
                continue

            try:
                parsed = quality._parse_payload(
                    payload,
                    visible_query,
                    engine,
                )
            except Exception:
                logging.exception(
                    "Failed to parse SerpAPI payload: engine=%s",
                    engine,
                )
                failed += 1
                continue

            for lead in parsed:
                try:
                    score = quality._quality(
                        lead,
                        primary,
                        secondary,
                        region,
                        engine,
                        priorities,
                    )
                    if score is None:
                        continue
                    if not _strong_target_match(
                        lead,
                        primary,
                        secondary,
                        engine,
                    ):
                        continue
                    if (
                        exclusion_separator
                        and exclusions.strip()
                        and _matches_exclusion(lead, exclusions)
                    ):
                        continue

                    lead.score = score
                    key = quality._dedupe_key(lead)
                    current = accepted.get(key)
                    if current is None or _sort_key(lead) > _sort_key(current):
                        accepted[key] = lead
                except Exception:
                    logging.exception(
                        "Skipped malformed lead candidate: engine=%s",
                        engine,
                    )

            if len(accepted) >= requested_limit:
                break

        self.last_search_diagnostics = {
            "attempted_plans": attempted,
            "failed_plans": failed,
            "accepted": len(accepted),
        }

        return sorted(
            accepted.values(),
            key=_sort_key,
            reverse=True,
        )[:requested_limit]

    serpapi_class.search = search
    serpapi_class._resilient_search_installed = True
