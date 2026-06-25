from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    niche: Mapped[str | None] = mapped_column(String(120), index=True)
    city: Mapped[str | None] = mapped_column(String(120), index=True)
    country: Mapped[str | None] = mapped_column(String(120), index=True)
    website_url: Mapped[str | None] = mapped_column(String(500))
    instagram_url: Mapped[str | None] = mapped_column(String(500))
    tiktok_url: Mapped[str | None] = mapped_column(String(500))
    telegram_url: Mapped[str | None] = mapped_column(String(500))
    vk_url: Mapped[str | None] = mapped_column(String(500))
    youtube_url: Mapped[str | None] = mapped_column(String(500))
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    phone: Mapped[str | None] = mapped_column(String(80), index=True)
    whatsapp: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    pain_points: Mapped[str | None] = mapped_column(Text)
    suggested_offer: Mapped[str | None] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    score_reason: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(500), index=True)
    source_type: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="new", index=True)
    first_found_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    notes: Mapped[str | None] = mapped_column(Text)


class LeadAudit(Base):
    __tablename__ = "lead_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    source_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class LeadWatch(Base):
    __tablename__ = "lead_watches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_user_id: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(160))
    niche: Mapped[str] = mapped_column(String(120), index=True)
    city: Mapped[str] = mapped_column(String(120), index=True)
    country: Mapped[str] = mapped_column(String(120), default="Россия")
    services_json: Mapped[str] = mapped_column(Text, default="[]")
    target_pain: Mapped[str] = mapped_column(Text, default="")
    exclude: Mapped[str] = mapped_column(Text, default="")
    min_score: Mapped[int] = mapped_column(Integer, default=60)
    result_limit: Mapped[int] = mapped_column(Integer, default=5)
    contacts_only: Mapped[bool] = mapped_column(Boolean, default=False)
    strict_match: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    total_runs: Mapped[int] = mapped_column(Integer, default=0)
    total_found: Mapped[int] = mapped_column(Integer, default=0)
    total_new: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class LeadWatchSeen(Base):
    __tablename__ = "lead_watch_seen"
    __table_args__ = (
        UniqueConstraint(
            "watch_id",
            "lead_id",
            name="uq_lead_watch_seen_watch_lead",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watch_id: Mapped[int] = mapped_column(
        ForeignKey("lead_watches.id", ondelete="CASCADE"),
        index=True,
    )
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"),
        index=True,
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )
    notified_at: Mapped[datetime | None] = mapped_column(DateTime)
