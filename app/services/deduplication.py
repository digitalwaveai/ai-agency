from urllib.parse import urlparse

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models import Lead
from app.schemas import LeadCreate


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None

    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/").lower()

    if not host:
        return None

    return f"{host}{path}"


def social_handle(url: str | None) -> str | None:
    if not url:
        return None

    path = urlparse(url).path.strip("/")

    if not path:
        return None

    return path.split("/")[0].lower()


def find_duplicate(
    db: Session,
    lead: LeadCreate,
) -> Lead | None:
    clauses = []

    if lead.email:
        clauses.append(Lead.email == lead.email)

    if lead.phone:
        clauses.append(Lead.phone == lead.phone)

    if lead.name and lead.city:
        clauses.append(
            and_(
                Lead.name == lead.name,
                Lead.city == lead.city,
            )
        )

    candidates = (
        db.query(Lead).filter(or_(*clauses)).all()
        if clauses
        else []
    )

    website_url = normalize_url(lead.website_url)
    source_url = normalize_url(lead.source_url)
    instagram_handle = social_handle(lead.instagram_url)
    telegram_handle = social_handle(lead.telegram_url)
    vk_handle = social_handle(lead.vk_url)

    if any([
        website_url,
        source_url,
        instagram_handle,
        telegram_handle,
        vk_handle,
    ]):
        for existing in db.query(Lead).all():
            if (
                website_url
                and normalize_url(existing.website_url) == website_url
            ):
                candidates.append(existing)

            if (
                source_url
                and normalize_url(existing.source_url) == source_url
            ):
                candidates.append(existing)

            if (
                instagram_handle
                and social_handle(existing.instagram_url)
                == instagram_handle
            ):
                candidates.append(existing)

            if (
                telegram_handle
                and social_handle(existing.telegram_url)
                == telegram_handle
            ):
                candidates.append(existing)

            if (
                vk_handle
                and social_handle(existing.vk_url) == vk_handle
            ):
                candidates.append(existing)

    return candidates[0] if candidates else None
