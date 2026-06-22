from datetime import datetime
from pydantic import BaseModel, Field, HttpUrl

class SearchRequest(BaseModel):
    niche: str
    city: str
    country: str = ""
    language: str = "ru"
    target_type: str = "частные эксперты"
    services: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=100)
    min_score: int = Field(default=0, ge=0, le=100)
    contacts_only: bool = False
    exclude: str = ""

class LeadBase(BaseModel):
    name: str

    lead_code: str | None = None

    niche: str | None = None
    city: str | None = None
    country: str | None = None
    website_url: str | None = None
    instagram_url: str | None = None
    tiktok_url: str | None = None
    telegram_url: str | None = None
    vk_url: str | None = None
    youtube_url: str | None = None
    email: str | None = None
    phone: str | None = None
    whatsapp: str | None = None
    description: str | None = None
    pain_points: str | None = None
    suggested_offer: str | None = None
    source_url: str
    source_type: str | None = None
    notes: str | None = None

class LeadCreate(LeadBase):
    score: int = 0
    score_reason: str | None = None
    status: str = "new"

class LeadRead(LeadCreate):
    id: int
    first_found_at: datetime
    last_checked_at: datetime | None = None
    last_updated_at: datetime
    model_config = {"from_attributes": True}

class LeadUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None

class OutreachResponse(BaseModel):

    premium: str
    soft: str
    business: str
    short: str
    follow_up: str
    specific_answer: str
    recommended_service: str

class LeadSearchItem(BaseModel):
    id: int
    lead_code: str | None = None
    name: str
    niche: str | None = None
    city: str | None = None
    score: int = 0
    model_config = {"from_attributes": True}

    soft: str
    business: str
    short: str

