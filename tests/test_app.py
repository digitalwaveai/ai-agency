from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from app.database import Base, get_db
from app.main import app
from app.models import Lead
from app.schemas import LeadCreate, SearchRequest
from app.services.deduplication import find_duplicate
from app.services.export import leads_to_csv
from app.services.scoring import score_lead
from app.services.search_service import generate_queries

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try: yield db
    finally: db.close()
app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

def sample_lead(**kw):
    data = dict(name="Косметолог Анна", niche="косметолог", city="Москва", country="Россия", website_url="https://anna.example.com", instagram_url="https://instagram.com/anna", email="anna@example.com", phone="+79990000000", whatsapp="+79990000000", description="Косметолог Москва, запись через WhatsApp, сайта нет, консультации", pain_points="ручная запись, нет сайта", suggested_offer="сайт", source_url="https://example.com/anna", source_type="demo")
    data.update(kw); return LeadCreate(**data)

def test_generate_queries():
    qs = generate_queries(SearchRequest(niche="косметолог", city="Москва", services=["сайт"]))
    assert any("косметолог Москва" in q for q in qs)
    assert any("WhatsApp" in q for q in qs)

def test_scoring():
    score, reason = score_lead(sample_lead(), SearchRequest(niche="косметолог", city="Москва"))
    assert score >= 80
    assert "контакт" in reason

def test_save_and_filter_lead():
    payload = {"niche":"косметолог","city":"Москва","country":"Россия","services":["сайт"],"limit":2,"min_score":0,"contacts_only":False}
    r = client.post("/search", json=payload)
    assert r.status_code == 200
    r = client.get("/leads", params={"city":"Москва", "min_score": 1})
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_deduplication_by_email():
    db = TestingSessionLocal()
    lead = Lead(**sample_lead(email="dup@example.com").model_dump(), score=90)
    db.add(lead); db.commit()
    assert find_duplicate(db, sample_lead(email="dup@example.com")) is not None
    db.close()

def test_export_csv():
    db = TestingSessionLocal()
    csv_text = leads_to_csv(db.query(Lead).all())
    assert "name" in csv_text and "source_url" in csv_text

def test_outreach_generation():
    r = client.get("/leads")
    if not r.json():
        client.post("/search", json={"niche":"косметолог","city":"Москва","limit":1})
        r = client.get("/leads")
    lead_id = r.json()[0]["id"]
    msg = client.post(f"/leads/{lead_id}/outreach")
    assert msg.status_code == 200
    assert {"soft", "business", "short"}.issubset(msg.json())
