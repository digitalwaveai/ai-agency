from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db, init_db
from app.models import Lead, LeadWatch, LeadWatchSeen
from app.schemas import (
    LeadAuditResponse,
    LeadRead,
    LeadUpdate,
    OutreachResponse,
    PendingWatchNotification,
    SearchRequest,
    WatchCreate,
    WatchRead,
    WatchRunResponse,
)
from app.services.audit_service import get_or_create_lead_audit
from app.services.export import leads_to_csv
from app.services.lead_pipeline import search_and_save_leads
from app.services.outreach_generator import generate_outreach
from app.services.search_service import generate_queries
from app.services.watch_service import (
    acknowledge_notification,
    create_watch,
    due_watches,
    get_owned_watch,
    pending_notifications,
    run_watch,
    watch_to_read,
)


app = FastAPI(title="Beauty Lead Finder Assistant")


@app.on_event("startup")
def startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html><head><title>Beauty Lead Finder Assistant</title></head>
    <body><h1>Beauty Lead Finder Assistant</h1>
    <p>Используйте <a href='/docs'>/docs</a> или Discord-бота.</p></body></html>
    """


@app.post("/queries")
def queries(req: SearchRequest):
    return {"queries": generate_queries(req)}


@app.post("/search", response_model=list[LeadRead])
async def search(
    req: SearchRequest,
    db: Session = Depends(get_db),
):
    return await search_and_save_leads(req, db)


@app.get("/leads", response_model=list[LeadRead])
def list_leads(
    niche: str | None = None,
    city: str | None = None,
    min_score: int = 0,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(Lead).filter(Lead.score >= min_score)
    if niche:
        query = query.filter(Lead.niche.ilike(f"%{niche}%"))
    if city:
        query = query.filter(Lead.city.ilike(f"%{city}%"))
    if status:
        query = query.filter(Lead.status == status)
    return query.order_by(
        Lead.score.desc(),
        Lead.last_updated_at.desc(),
    ).all()


@app.get("/leads/{lead_id}", response_model=LeadRead)
def get_lead(
    lead_id: int,
    db: Session = Depends(get_db),
):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return lead


@app.patch("/leads/{lead_id}", response_model=LeadRead)
def update_lead(
    lead_id: int,
    payload: LeadUpdate,
    db: Session = Depends(get_db),
):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(lead, key, value)
    db.commit()
    db.refresh(lead)
    return lead


@app.post(
    "/leads/{lead_id}/audit",
    response_model=LeadAuditResponse,
)
def lead_audit(
    lead_id: int,
    force_refresh: bool = False,
    db: Session = Depends(get_db),
):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return get_or_create_lead_audit(
        db,
        lead,
        force_refresh=force_refresh,
    )


@app.post(
    "/leads/{lead_id}/outreach",
    response_model=OutreachResponse,
)
def outreach(
    lead_id: int,
    db: Session = Depends(get_db),
):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return generate_outreach(lead)


@app.post("/watches", response_model=WatchRead)
def create_lead_watch(
    payload: WatchCreate,
    db: Session = Depends(get_db),
):
    return watch_to_read(create_watch(db, payload))


@app.get("/watches", response_model=list[WatchRead])
def list_lead_watches(
    owner_user_id: str,
    db: Session = Depends(get_db),
):
    watches = (
        db.query(LeadWatch)
        .filter(LeadWatch.owner_user_id == owner_user_id)
        .order_by(LeadWatch.created_at.desc())
        .all()
    )
    return [watch_to_read(watch) for watch in watches]


@app.get("/watches/due", response_model=list[WatchRead])
def list_due_watches(
    limit: int = 20,
    db: Session = Depends(get_db),
):
    return [
        watch_to_read(watch)
        for watch in due_watches(
            db,
            limit=max(1, min(limit, 100)),
        )
    ]


@app.post(
    "/watches/{watch_id}/run",
    response_model=WatchRunResponse,
)
async def run_owned_watch(
    watch_id: int,
    owner_user_id: str,
    db: Session = Depends(get_db),
):
    watch = get_owned_watch(
        db,
        watch_id,
        owner_user_id,
    )
    if watch is None:
        raise HTTPException(404, "Радар не найден")

    try:
        found, new_leads = await run_watch(
            db,
            watch,
            scheduled=False,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

    return WatchRunResponse(
        watch=watch_to_read(watch),
        found_count=len(found),
        new_count=len(new_leads),
        new_leads=new_leads,
    )


@app.post(
    "/watches/{watch_id}/run-scheduled",
    response_model=WatchRunResponse,
)
async def run_scheduled_watch(
    watch_id: int,
    db: Session = Depends(get_db),
):
    watch = db.get(LeadWatch, watch_id)
    if watch is None:
        raise HTTPException(404, "Радар не найден")

    try:
        found, new_leads = await run_watch(
            db,
            watch,
            scheduled=True,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc

    return WatchRunResponse(
        watch=watch_to_read(watch),
        found_count=len(found),
        new_count=len(new_leads),
        new_leads=new_leads,
    )


@app.post(
    "/watches/{watch_id}/pause",
    response_model=WatchRead,
)
def pause_watch(
    watch_id: int,
    owner_user_id: str,
    db: Session = Depends(get_db),
):
    watch = get_owned_watch(
        db,
        watch_id,
        owner_user_id,
    )
    if watch is None:
        raise HTTPException(404, "Радар не найден")

    watch.is_active = False
    db.add(watch)
    db.commit()
    db.refresh(watch)
    return watch_to_read(watch)


@app.post(
    "/watches/{watch_id}/resume",
    response_model=WatchRead,
)
def resume_watch(
    watch_id: int,
    owner_user_id: str,
    db: Session = Depends(get_db),
):
    watch = get_owned_watch(
        db,
        watch_id,
        owner_user_id,
    )
    if watch is None:
        raise HTTPException(404, "Радар не найден")

    from datetime import datetime

    watch.is_active = True
    watch.next_run_at = datetime.utcnow()
    db.add(watch)
    db.commit()
    db.refresh(watch)
    return watch_to_read(watch)


@app.delete("/watches/{watch_id}")
def delete_watch(
    watch_id: int,
    owner_user_id: str,
    db: Session = Depends(get_db),
):
    watch = get_owned_watch(
        db,
        watch_id,
        owner_user_id,
    )
    if watch is None:
        raise HTTPException(404, "Радар не найден")

    (
        db.query(LeadWatchSeen)
        .filter(LeadWatchSeen.watch_id == watch_id)
        .delete(synchronize_session=False)
    )
    db.delete(watch)
    db.commit()
    return {"deleted": True, "watch_id": watch_id}


@app.get(
    "/watch-notifications/pending",
    response_model=list[PendingWatchNotification],
)
def list_pending_watch_notifications(
    limit: int = 50,
    db: Session = Depends(get_db),
):
    records = pending_notifications(
        db,
        limit=max(1, min(limit, 200)),
    )

    return [
        PendingWatchNotification(
            watch_id=watch.id,
            watch_name=watch.name,
            owner_user_id=watch.owner_user_id,
            lead=lead,
            first_seen_at=seen.first_seen_at,
        )
        for seen, watch, lead in records
    ]


@app.post(
    "/watch-notifications/{watch_id}/{lead_id}/ack",
)
def acknowledge_watch_notification(
    watch_id: int,
    lead_id: int,
    db: Session = Depends(get_db),
):
    acknowledged = acknowledge_notification(
        db,
        watch_id=watch_id,
        lead_id=lead_id,
    )
    if not acknowledged:
        raise HTTPException(
            404,
            "Ожидающее уведомление не найдено",
        )
    return {"acknowledged": True}


@app.get("/export.csv")
def export_csv(
    db: Session = Depends(get_db),
):
    return Response(
        leads_to_csv(db.query(Lead).all()),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=leads.csv",
        },
    )
