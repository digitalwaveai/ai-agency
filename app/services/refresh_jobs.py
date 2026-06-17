from datetime import datetime, timedelta
import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
from app.models import Lead
from app.services.scoring import score_lead
from app.schemas import LeadCreate

async def check_url(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            return (await client.get(url)).status_code < 400
    except Exception:
        return False

def refresh_old_leads(db: Session, older_than_hours: int = 24) -> int:
    cutoff = datetime.utcnow() - timedelta(hours=older_than_hours)
    leads = db.query(Lead).filter((Lead.last_checked_at == None) | (Lead.last_checked_at < cutoff)).all()  # noqa: E711
    for lead in leads:
        dto = LeadCreate.model_validate(lead, from_attributes=True)
        lead.score, lead.score_reason = score_lead(dto)
        lead.last_checked_at = datetime.utcnow()
        if lead.score < 20 and lead.status == "new":
            lead.status = "archived"
    db.commit()
    return len(leads)

def start_scheduler(session_factory, interval_hours: int):
    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: refresh_old_leads(session_factory(), interval_hours), "interval", hours=interval_hours)
    scheduler.start()
    return scheduler
