from __future__ import annotations

from functools import wraps
from typing import Any

from .project_search_context import (
    EXCLUSION_SEPARATOR,
    PRIORITY_SEPARATOR,
    TARGET_SEPARATOR,
)


def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _split_target(target: str) -> tuple[str, str, str, str]:
    search_part, exclusion_separator, exclusions = str(target or "").partition(
        EXCLUSION_SEPARATOR
    )
    target_part, priority_separator, priorities = search_part.partition(
        PRIORITY_SEPARATOR
    )
    primary, target_separator, secondary = target_part.partition(TARGET_SEPARATOR)
    return (
        _clean(primary),
        _clean(secondary) if target_separator else "",
        _clean(priorities) if priority_separator else "",
        _clean(exclusions) if exclusion_separator else "",
    )


def _compose_target(
    primary: str,
    *,
    secondary: str = "",
    exclusions: str = "",
) -> str:
    target = _clean(primary)
    if secondary:
        target = f"{target}{TARGET_SEPARATOR}{_clean(secondary)}"
    if exclusions:
        target = f"{target}{EXCLUSION_SEPARATOR}{_clean(exclusions)}"
    return target


def _fallback_targets(target: str) -> list[str]:
    primary, secondary, priorities, exclusions = _split_target(target)
    if not primary:
        return []

    candidates: list[str] = []

    # First relax only optional project priorities. The requested segment,
    # project niche, region and explicit exclusions remain unchanged.
    if priorities:
        candidates.append(
            _compose_target(
                primary,
                secondary=secondary,
                exclusions=exclusions,
            )
        )

    # If the project niche made the query too overloaded, retry using the exact
    # segment entered by the user. Explicit exclusions are still preserved.
    if secondary:
        candidates.append(_compose_target(primary, exclusions=exclusions))

    original = _clean(target)
    result: list[str] = []
    seen: set[str] = {original}
    for candidate in candidates:
        normalized = _clean(candidate)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result[:2]


def _diagnostics(client: Any) -> dict[str, Any]:
    value = getattr(client, "last_search_diagnostics", {})
    return dict(value) if isinstance(value, dict) else {}


def _all_plans_failed(diagnostics: dict[str, Any]) -> bool:
    attempted = int(diagnostics.get("attempted_plans") or 0)
    failed = int(diagnostics.get("failed_plans") or 0)
    return attempted > 0 and failed >= attempted


def install_zero_result_fallback(serpapi_class: type[Any]) -> None:
    """Retry an over-constrained search without weakening junk filtering."""
    if getattr(serpapi_class, "_zero_result_fallback_installed", False):
        return

    previous_search = serpapi_class.search

    @wraps(previous_search)
    def search(
        self: Any,
        target: str,
        region: str,
        limit: int = 5,
    ):
        leads = previous_search(self, target, region, limit)
        initial_diagnostics = _diagnostics(self)
        if leads or _all_plans_failed(initial_diagnostics):
            return leads

        attempted_total = int(initial_diagnostics.get("attempted_plans") or 0)
        failed_total = int(initial_diagnostics.get("failed_plans") or 0)
        fallback_attempts = 0

        for fallback_target in _fallback_targets(target):
            fallback_attempts += 1
            leads = previous_search(self, fallback_target, region, limit)
            current = _diagnostics(self)
            attempted_total += int(current.get("attempted_plans") or 0)
            failed_total += int(current.get("failed_plans") or 0)
            if leads:
                self.last_search_diagnostics = {
                    **current,
                    "attempted_plans": attempted_total,
                    "failed_plans": failed_total,
                    "accepted": len(leads),
                    "fallback_used": True,
                    "fallback_attempts": fallback_attempts,
                }
                return leads
            if _all_plans_failed(current):
                break

        self.last_search_diagnostics = {
            **initial_diagnostics,
            "attempted_plans": attempted_total,
            "failed_plans": failed_total,
            "accepted": 0,
            "fallback_used": fallback_attempts > 0,
            "fallback_attempts": fallback_attempts,
        }
        return []

    serpapi_class.search = search
    serpapi_class._zero_result_fallback_installed = True
