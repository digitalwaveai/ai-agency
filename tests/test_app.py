import atexit
import os
import tempfile
from pathlib import Path

_TEST_TMPDIR = tempfile.TemporaryDirectory()
TEST_DB_PATH = Path(_TEST_TMPDIR.name) / "test_leads.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"
os.environ["DEMO_MODE"] = "true"
os.environ["SEARCH_PROVIDER"] = "demo"
atexit.register(_TEST_TMPDIR.cleanup)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base, assign_lead_code, assign_missing_lead_codes, get_db
from app.main import app
from app.models import Lead
from app.schemas import LeadCreate, SearchRequest
from app.services.deduplication import find_duplicate
from app.services.export import leads_to_csv
from app.services.scoring import score_lead
from app.services.search_service import generate_queries

engine = create_engine(
    f"sqlite:///{TEST_DB_PATH}",
    connect_args={"check_same_thread": False},
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def reset_test_db() -> None:
    # Import models before create_all so the leads table is registered on Base.metadata.
    import app.models  # noqa: F401

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def test_database():
    reset_test_db()
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def sample_lead(**kw):
    data = dict(
        name="Косметолог Анна",
        niche="косметолог",
        city="Москва",
        country="Россия",
        website_url="https://anna.example.com",
        instagram_url="https://instagram.com/anna",
        email="anna@example.com",
        phone="+79990000000",
        whatsapp="+79990000000",
        description="Косметолог Москва, запись через WhatsApp, сайта нет, консультации",
        pain_points="ручная запись, нет сайта",
        suggested_offer="сайт",
        source_url="https://example.com/anna",
        source_type="demo",
        score=0,
    )
    data.update(kw)
    return LeadCreate(**data)


def test_generate_queries():
    qs = generate_queries(SearchRequest(niche="косметолог", city="Москва", services=["сайт"]))
    assert any("косметолог Москва" in q for q in qs)
    assert any("WhatsApp" in q for q in qs)


def test_scoring():
    score, reason = score_lead(sample_lead(), SearchRequest(niche="косметолог", city="Москва"))
    assert score >= 80
    assert "контакт" in reason


def test_save_and_filter_lead():
    payload = {"niche": "косметолог", "city": "Москва", "country": "Россия", "services": ["сайт"], "limit": 2, "min_score": 0, "contacts_only": False}
    r = client.post("/search", json=payload)
    assert r.status_code == 200
    assert len(r.json()) >= 1

    r = client.get("/leads", params={"city": "Москва", "min_score": 1})
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert all(item["city"] == "Москва" for item in r.json())


def test_deduplication_by_email():
    db = TestingSessionLocal()
    lead_data = sample_lead(email="dup@example.com", score=90).model_dump()
    lead = Lead(**lead_data)
    db.add(lead)
    db.commit()

    assert find_duplicate(db, sample_lead(email="dup@example.com")) is not None
    db.close()


def test_export_csv():
    db = TestingSessionLocal()
    db.add(Lead(**sample_lead(score=88).model_dump()))
    db.commit()

    csv_text = leads_to_csv(db.query(Lead).all())
    assert "name" in csv_text
    assert "source_url" in csv_text
    assert "Косметолог Анна" in csv_text
    db.close()


def test_outreach_generation():
    client.post("/search", json={"niche": "косметолог", "city": "Москва", "limit": 1})
    r = client.get("/leads")
    lead_id = r.json()[0]["id"]

    msg = client.post(f"/leads/{lead_id}/outreach")
    assert msg.status_code == 200
    assert {"soft", "business", "short"}.issubset(msg.json())


def test_new_lead_gets_unique_lead_code():
    db = TestingSessionLocal()
    lead_one = Lead(**sample_lead(name="Лид 1", email="one@example.com", source_url="https://example.com/one").model_dump())
    lead_two = Lead(**sample_lead(name="Лид 2", email="two@example.com", source_url="https://example.com/two").model_dump())
    db.add_all([lead_one, lead_two])
    db.commit()
    db.refresh(lead_one); db.refresh(lead_two)

    assign_lead_code(db, lead_one)
    assign_lead_code(db, lead_two)

    assert lead_one.lead_code == f"BLF-{lead_one.id:06d}"
    assert lead_two.lead_code == f"BLF-{lead_two.id:06d}"
    assert lead_one.lead_code != lead_two.lead_code
    db.close()


def test_updating_lead_does_not_change_lead_code():
    db = TestingSessionLocal()
    lead = Lead(**sample_lead(score=40).model_dump())
    db.add(lead); db.commit(); db.refresh(lead)
    assign_lead_code(db, lead)
    original_code = lead.lead_code

    lead.score = 99
    lead.notes = "updated"
    db.add(lead); db.commit(); db.refresh(lead)
    assign_lead_code(db, lead)

    assert lead.lead_code == original_code
    assert lead.score == 99
    db.close()


def test_existing_leads_get_code_without_losing_data():
    db = TestingSessionLocal()
    lead = Lead(**sample_lead(name="Существующий лид", score=77).model_dump())
    lead.lead_code = None
    db.add(lead); db.commit(); db.refresh(lead)

    assign_missing_lead_codes(db)
    db.refresh(lead)

    assert lead.lead_code == f"BLF-{lead.id:06d}"
    assert lead.name == "Существующий лид"
    assert lead.score == 77
    db.close()


def test_search_by_lead_code_returns_right_lead():
    client.post("/search", json={"niche": "косметолог", "city": "Москва", "limit": 1})
    lead = client.get("/leads").json()[0]

    response = client.get(f"/leads/by-code/{lead['lead_code']}")

    assert response.status_code == 200
    assert response.json()["id"] == lead["id"]


def test_autocomplete_searches_by_code_name_niche_and_city():
    client.post("/search", json={"niche": "косметолог", "city": "Москва", "limit": 1})
    lead = client.get("/leads").json()[0]

    by_code = client.get("/leads/search", params={"q": lead["lead_code"]}).json()
    by_name = client.get("/leads/search", params={"q": lead["name"].split()[0]}).json()
    by_niche = client.get("/leads/search", params={"q": "косметолог"}).json()
    by_city = client.get("/leads/search", params={"q": "Москва"}).json()

    assert by_code[0]["lead_code"] == lead["lead_code"]
    assert by_name
    assert by_niche
    assert by_city


def test_outreach_by_code_loads_data_from_database():
    client.post("/search", json={"niche": "косметолог", "city": "Москва", "limit": 1})
    lead = client.get("/leads").json()[0]

    response = client.post(f"/leads/by-code/{lead['lead_code']}/outreach")

    assert response.status_code == 200
    data = response.json()
    assert lead["name"] in data["premium"]
    assert "recommended_service" in data
    assert data["specific_answer"]


def test_outreach_survives_missing_optional_fields():
    db = TestingSessionLocal()
    lead = Lead(**sample_lead(website_url=None, instagram_url=None, telegram_url=None, email=None, phone=None, whatsapp=None, pain_points=None, suggested_offer=None, score=15).model_dump())
    db.add(lead); db.commit(); db.refresh(lead)
    assign_lead_code(db, lead)
    code = lead.lead_code
    db.close()

    response = client.post(f"/leads/by-code/{code}/outreach")

    assert response.status_code == 200
    assert response.json()["premium"]


def test_discord_lead_format_deduplicates_leads():
    from app.discord_bot import format_leads

    lead = {"id": 1, "lead_code": "BLF-000001", "name": "Анна", "niche": "косметолог", "city": "Москва", "score": 80, "status": "new", "website_url": "https://example.com"}
    text = format_leads([lead, lead], limit=10)

    assert text.count("BLF-000001") == 1


def test_discord_message_helpers_do_not_require_manual_client_fields():
    from app.discord_bot import confirmation_text, fallback_offer

    lead = {"id": 1, "lead_code": "BLF-000001", "name": "Анна", "niche": "косметолог", "city": "Москва", "website_url": None, "pain_points": "ручная запись"}

    card = confirmation_text(lead)
    offer = fallback_offer(lead)

    assert "BLF-000001" in card
    assert "Анна" in offer["premium"]
    assert offer["recommended_service"] in {"website", "booking_automation", "audit"}
