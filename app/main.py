from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from app.database import get_db, init_db
from app.models import Lead
from app.schemas import LeadRead, LeadUpdate, SearchRequest, OutreachResponse
from app.services.search_service import SearchService, generate_queries
from app.services.lead_enrichment import result_to_lead
from app.services.scoring import score_lead
from app.services.deduplication import find_duplicate
from app.services.outreach_generator import generate_outreach
from app.services.export import leads_to_csv

app = FastAPI(title="Beauty Lead Finder Assistant")

@app.on_event("startup")
def startup():
    init_db()

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html><head><title>Beauty Lead Finder Assistant</title><style>body{font-family:Inter,Arial;margin:40px;max-width:1100px}input,textarea,button,select{padding:10px;margin:4px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px}</style></head>
    <body><h1>Beauty Lead Finder Assistant</h1><p>MVP API готов. Используйте <a href='/docs'>/docs</a> или Streamlit UI.</p>
    <form onsubmit="event.preventDefault();fetch('/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({niche:niche.value,city:city.value,country:country.value,services:services.value.split(','),limit:Number(limit.value),min_score:Number(min_score.value),contacts_only:contacts.checked})}).then(r=>r.json()).then(j=>out.textContent=JSON.stringify(j,null,2))">
    <input id=niche placeholder='ниша' value='косметолог'><input id=city placeholder='город' value='Москва'><input id=country placeholder='страна' value='Россия'><input id=services placeholder='услуги через запятую' value='сайт,Telegram-бот'><input id=limit type=number value=5><input id=min_score type=number value=0><label><input id=contacts type=checkbox> только с контактами</label><button>Найти лиды</button></form><pre id=out></pre></body></html>"""

@app.post("/queries")
def queries(req: SearchRequest):
    return {"queries": generate_queries(req)}

@app.post("/search", response_model=list[LeadRead])
async def search(req: SearchRequest, db: Session = Depends(get_db)):
    results = await SearchService().search(req)
    saved = []
    for result in results:
        lead_in = result_to_lead(result, req)
        lead_in.score, lead_in.score_reason = score_lead(lead_in, req)
        if lead_in.score < req.min_score: continue
        if req.contacts_only and not any([lead_in.email, lead_in.phone, lead_in.telegram_url, lead_in.whatsapp and lead_in.whatsapp != "не найден"]): continue
        dup = find_duplicate(db, lead_in)
        if dup:
            saved.append(dup); continue
        lead = Lead(**lead_in.model_dump())
        db.add(lead); db.commit(); db.refresh(lead)
        saved.append(lead)
    return saved

@app.get("/leads", response_model=list[LeadRead])
def list_leads(niche: str | None = None, city: str | None = None, min_score: int = 0, status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Lead).filter(Lead.score >= min_score)
    if niche: q = q.filter(Lead.niche.ilike(f"%{niche}%"))
    if city: q = q.filter(Lead.city.ilike(f"%{city}%"))
    if status: q = q.filter(Lead.status == status)
    return q.order_by(Lead.score.desc(), Lead.last_updated_at.desc()).all()

@app.get("/leads/{lead_id}", response_model=LeadRead)
def get_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead: raise HTTPException(404, "Lead not found")
    return lead

@app.patch("/leads/{lead_id}", response_model=LeadRead)
def update_lead(lead_id: int, payload: LeadUpdate, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead: raise HTTPException(404, "Lead not found")
    for k, v in payload.model_dump(exclude_unset=True).items(): setattr(lead, k, v)
    db.commit(); db.refresh(lead); return lead

@app.post("/leads/{lead_id}/outreach", response_model=OutreachResponse)
def outreach(lead_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead: raise HTTPException(404, "Lead not found")
    return generate_outreach(lead)

@app.get("/export.csv")
def export_csv(db: Session = Depends(get_db)):
    return Response(leads_to_csv(db.query(Lead).all()), media_type="text/csv", headers={"Content-Disposition":"attachment; filename=leads.csv"})
