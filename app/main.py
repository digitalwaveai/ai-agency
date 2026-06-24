from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db, init_db
from app.models import Lead
from app.schemas import LeadRead, LeadUpdate, OutreachResponse, SearchRequest
from app.services.deduplication import find_duplicate
from app.services.export import leads_to_csv
from app.services.lead_enrichment import result_to_lead
from app.services.outreach_generator import generate_outreach
from app.services.scoring import score_lead
from app.services.search_quality import offer_is_relevant, pain_is_confirmed
from app.services.search_service import SearchService, generate_queries
from app.services.site_enrichment import enrich_leads_from_web
from app.services.smart_analysis import analyze_and_filter_leads


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
    results = await SearchService().search(req)
    lead_inputs = [result_to_lead(result, req) for result in results]
    lead_inputs = await enrich_leads_from_web(lead_inputs)
    lead_inputs = await analyze_and_filter_leads(
        lead_inputs,
        niche=req.niche,
        city=req.city,
        target_pain=req.target_pain,
        services=req.services,
        exclude=req.exclude,
        strict_match=req.strict_match,
    )

    effective_min_score = max(
        req.min_score,
        60 if req.strict_match else req.min_score,
    )
    qualified = []

    for lead_in in lead_inputs:
        lead_in.score, lead_in.score_reason = score_lead(lead_in, req)

        if lead_in.score < effective_min_score:
            continue

        if req.strict_match and req.target_pain and not pain_is_confirmed(lead_in.pain_points):
            continue

        if req.strict_match and req.services and not offer_is_relevant(lead_in.suggested_offer):
            continue

        has_contact = any(
            [
                lead_in.email,
                lead_in.phone,
                lead_in.telegram_url,
                lead_in.instagram_url,
                lead_in.vk_url,
                lead_in.whatsapp,
            ]
        )

        if req.contacts_only and not has_contact:
            continue

        qualified.append(lead_in)

    qualified.sort(key=lambda item: item.score, reverse=True)
    qualified = qualified[: req.limit]
    saved = []

    for lead_in in qualified:
        duplicate = find_duplicate(db, lead_in)

        if duplicate:
            refresh_fields = (
                "name",
                "website_url",
                "instagram_url",
                "telegram_url",
                "vk_url",
                "email",
                "phone",
                "whatsapp",
                "description",
                "pain_points",
                "suggested_offer",
                "source_url",
                "source_type",
                "score",
                "score_reason",
            )

            for field_name in refresh_fields:
                value = getattr(lead_in, field_name, None)
                if value not in (None, "", "не найден"):
                    setattr(duplicate, field_name, value)

            db.add(duplicate)
            db.commit()
            db.refresh(duplicate)
            saved.append(duplicate)
            continue

        lead = Lead(**lead_in.model_dump())
        db.add(lead)
        db.commit()
        db.refresh(lead)
        saved.append(lead)

    unique_leads = {lead.id: lead for lead in saved}
    return list(unique_leads.values())


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
    return query.order_by(Lead.score.desc(), Lead.last_updated_at.desc()).all()


@app.get("/leads/{lead_id}", response_model=LeadRead)
def get_lead(lead_id: int, db: Session = Depends(get_db)):
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


@app.post("/leads/{lead_id}/outreach", response_model=OutreachResponse)
def outreach(lead_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    return generate_outreach(lead)


@app.get("/export.csv")
def export_csv(db: Session = Depends(get_db)):
    return Response(
        leads_to_csv(db.query(Lead).all()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )
