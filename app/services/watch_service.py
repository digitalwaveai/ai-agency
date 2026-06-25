from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models import Lead, LeadWatch, LeadWatchSeen
from app.schemas import SearchRequest, WatchCreate, WatchRead
from app.services.lead_pipeline import search_and_save_leads


def watch_services(watch: LeadWatch) -> list[str]:
    try:
        value = json.loads(watch.services_json or "[]")
    except json.JSONDecodeError:
        value = []

    return [
        str(item).strip()
        for item in value
        if str(item).strip()
    ]


def watch_to_read(watch: LeadWatch) -> WatchRead:
    return WatchRead(
        id=watch.id,
        owner_user_id=watch.owner_user_id,
        name=watch.name,
        niche=watch.niche,
        city=watch.city,
        country=watch.country,
        services=watch_services(watch),
        target_pain=watch.target_pain,
        exclude=watch.exclude,
        min_score=watch.min_score,
        result_limit=watch.result_limit,
        contacts_only=watch.contacts_only,
        strict_match=watch.strict_match,
        interval_hours=watch.interval_hours,
        is_active=watch.is_active,
        total_runs=watch.total_runs,
        total_found=watch.total_found,
        total_new=watch.total_new,
        last_run_at=watch.last_run_at,
        next_run_at=watch.next_run_at,
        last_error=watch.last_error,
        created_at=watch.created_at,
        updated_at=watch.updated_at,
    )


def create_watch(db: Session, payload: WatchCreate) -> LeadWatch:
    now = datetime.utcnow()
    name = payload.name or (
        f"{payload.niche} — {payload.city} — {payload.target_pain}"
    )

    watch = LeadWatch(
        owner_user_id=payload.owner_user_id,
        name=name[:160],
        niche=payload.niche,
        city=payload.city,
        country=payload.country,
        services_json=json.dumps(
            payload.services,
            ensure_ascii=False,
        ),
        target_pain=payload.target_pain,
        exclude=payload.exclude,
        min_score=payload.min_score,
        result_limit=payload.result_limit,
        contacts_only=payload.contacts_only,
        strict_match=payload.strict_match,
        interval_hours=payload.interval_hours,
        is_active=True,
        next_run_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(watch)
    db.commit()
    db.refresh(watch)
    return watch


def get_owned_watch(
    db: Session,
    watch_id: int,
    owner_user_id: str,
) -> LeadWatch | None:
    return (
        db.query(LeadWatch)
        .filter(
            LeadWatch.id == watch_id,
            LeadWatch.owner_user_id == owner_user_id,
        )
        .one_or_none()
    )


def watch_search_request(watch: LeadWatch) -> SearchRequest:
    return SearchRequest(
        niche=watch.niche,
        city=watch.city,
        country=watch.country,
        services=watch_services(watch),
        target_pain=watch.target_pain,
        limit=watch.result_limit,
        min_score=watch.min_score,
        contacts_only=watch.contacts_only,
        strict_match=watch.strict_match,
        exclude=watch.exclude,
        language="ru",
        target_type="частные эксперты",
    )


async def run_watch(
    db: Session,
    watch: LeadWatch,
    *,
    scheduled: bool,
) -> tuple[list[Lead], list[Lead]]:
    now = datetime.utcnow()

    if not watch.is_active:
        raise ValueError("Радар находится на паузе")

    try:
        found = await search_and_save_leads(
            watch_search_request(watch),
            db,
        )
        new_leads: list[Lead] = []

        for lead in found:
            seen = (
                db.query(LeadWatchSeen)
                .filter(
                    LeadWatchSeen.watch_id == watch.id,
                    LeadWatchSeen.lead_id == lead.id,
                )
                .one_or_none()
            )

            if seen is not None:
                continue

            seen = LeadWatchSeen(
                watch_id=watch.id,
                lead_id=lead.id,
                first_seen_at=now,
                notified_at=None if scheduled else now,
            )
            db.add(seen)
            new_leads.append(lead)

        watch.total_runs += 1
        watch.total_found += len(found)
        watch.total_new += len(new_leads)
        watch.last_run_at = now
        watch.next_run_at = now + timedelta(
            hours=watch.interval_hours,
        )
        watch.last_error = None
        watch.updated_at = now

        db.add(watch)
        db.commit()
        db.refresh(watch)

        return found, new_leads

    except Exception as exc:
        watch.total_runs += 1
        watch.last_run_at = now
        watch.next_run_at = now + timedelta(
            hours=watch.interval_hours,
        )
        watch.last_error = str(exc)[:1000]
        watch.updated_at = now
        db.add(watch)
        db.commit()
        raise


def due_watches(
    db: Session,
    *,
    limit: int = 20,
) -> list[LeadWatch]:
    now = datetime.utcnow()
    return (
        db.query(LeadWatch)
        .filter(
            LeadWatch.is_active.is_(True),
            LeadWatch.next_run_at.is_not(None),
            LeadWatch.next_run_at <= now,
        )
        .order_by(LeadWatch.next_run_at.asc())
        .limit(limit)
        .all()
    )


def pending_notifications(
    db: Session,
    *,
    limit: int = 50,
) -> list[tuple[LeadWatchSeen, LeadWatch, Lead]]:
    return (
        db.query(LeadWatchSeen, LeadWatch, Lead)
        .join(
            LeadWatch,
            LeadWatch.id == LeadWatchSeen.watch_id,
        )
        .join(
            Lead,
            Lead.id == LeadWatchSeen.lead_id,
        )
        .filter(LeadWatchSeen.notified_at.is_(None))
        .order_by(LeadWatchSeen.first_seen_at.asc())
        .limit(limit)
        .all()
    )


def acknowledge_notification(
    db: Session,
    *,
    watch_id: int,
    lead_id: int,
) -> bool:
    record = (
        db.query(LeadWatchSeen)
        .filter(
            LeadWatchSeen.watch_id == watch_id,
            LeadWatchSeen.lead_id == lead_id,
            LeadWatchSeen.notified_at.is_(None),
        )
        .one_or_none()
    )

    if record is None:
        return False

    record.notified_at = datetime.utcnow()
    db.add(record)
    db.commit()
    return True
