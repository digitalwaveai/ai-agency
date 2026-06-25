from datetime import datetime

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    niche: str
    city: str
    country: str = ""
    language: str = "ru"
    target_type: str = "частные эксперты"
    services: list[str] = Field(default_factory=list)
    target_pain: str = ""
    limit: int = Field(default=10, ge=1, le=100)
    min_score: int = Field(default=0, ge=0, le=100)
    contacts_only: bool = False
    exclude: str = ""
    strict_match: bool = True


class LeadBase(BaseModel):
    name: str
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
    soft: str
    business: str
    short: str


class LeadAuditResponse(BaseModel):
    lead_id: int
    lead_name: str
    classification: str
    confidence: int = Field(ge=0, le=100)
    fit_level: str
    why_fit: list[str]
    evidence: str | None = None
    existing_assets: list[str]
    do_not_offer: list[str]
    best_offer: list[str]
    first_message: str
    warnings: list[str]
    generated_at: datetime
    cached: bool


class WatchCreate(BaseModel):
    owner_user_id: str
    name: str | None = None
    niche: str
    city: str
    country: str = "Россия"
    services: list[str] = Field(default_factory=list)
    target_pain: str = "запись через личные сообщения"
    exclude: str = (
        "крупные сети, франшизы, филиалы, холдинги, агентства, "
        "каталоги, сайты отзывов, агрегаторы"
    )
    min_score: int = Field(default=60, ge=0, le=100)
    result_limit: int = Field(default=5, ge=1, le=25)
    contacts_only: bool = False
    strict_match: bool = False
    interval_hours: int = Field(default=24, ge=1, le=168)


class WatchUpdate(BaseModel):
    name: str | None = None
    min_score: int | None = Field(default=None, ge=0, le=100)
    result_limit: int | None = Field(default=None, ge=1, le=25)
    interval_hours: int | None = Field(default=None, ge=1, le=168)
    is_active: bool | None = None


class WatchRead(BaseModel):
    id: int
    owner_user_id: str
    name: str
    niche: str
    city: str
    country: str
    services: list[str]
    target_pain: str
    exclude: str
    min_score: int
    result_limit: int
    contacts_only: bool
    strict_match: bool
    interval_hours: int
    is_active: bool
    total_runs: int
    total_found: int
    total_new: int
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class WatchRunResponse(BaseModel):
    watch: WatchRead
    found_count: int
    new_count: int
    new_leads: list[LeadRead]


class PendingWatchNotification(BaseModel):
    watch_id: int
    watch_name: str
    owner_user_id: str
    lead: LeadRead
    first_seen_at: datetime
