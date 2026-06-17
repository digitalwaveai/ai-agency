from urllib.parse import urlparse
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session
from app.models import Lead
from app.schemas import LeadCreate

def domain(url: str | None) -> str | None:
    if not url: return None
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return host or None

def handle(url: str | None) -> str | None:
    if not url: return None
    return urlparse(url).path.strip("/").split("/")[0].lower() or None

def find_duplicate(db: Session, lead: LeadCreate) -> Lead | None:
    clauses = []
    if lead.email: clauses.append(Lead.email == lead.email)
    if lead.phone: clauses.append(Lead.phone == lead.phone)
    if lead.name and lead.city: clauses.append(and_(Lead.name == lead.name, Lead.city == lead.city))
    ig = handle(lead.instagram_url)
    tg = handle(lead.telegram_url)
    candidates = db.query(Lead).filter(or_(*clauses)).all() if clauses else []
    site_domain = domain(lead.website_url)
    for existing in db.query(Lead).all() if any([site_domain, ig, tg]) else []:
        if site_domain and domain(existing.website_url) == site_domain: candidates.append(existing)
        if ig and handle(existing.instagram_url) == ig: candidates.append(existing)
        if tg and handle(existing.telegram_url) == tg: candidates.append(existing)
    return candidates[0] if candidates else None
