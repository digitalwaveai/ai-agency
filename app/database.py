
from sqlalchemy import create_engine, text


from sqlalchemy import create_engine, text

from sqlalchemy import create_engine


from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import get_settings

class Base(DeclarativeBase):
    pass

def _connect_args(url: str) -> dict:
    return {"check_same_thread": False} if url.startswith("sqlite") else {}

engine = create_engine(get_settings().database_url, connect_args=_connect_args(get_settings().database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

_initialized = False

def format_lead_code(lead_id: int) -> str:
    return f"BLF-{lead_id:06d}"

def ensure_lead_code_column() -> None:
    """Safely add and backfill lead_code for existing SQLite databases."""
    if not engine.url.get_backend_name().startswith("sqlite"):
        return
    with engine.begin() as connection:
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(leads)").fetchall()}
        if "lead_code" not in columns:
            connection.exec_driver_sql("ALTER TABLE leads ADD COLUMN lead_code VARCHAR(20)")
        connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS ix_leads_lead_code ON leads (lead_code)")
        rows = connection.execute(text("SELECT id FROM leads WHERE lead_code IS NULL OR lead_code = '' ORDER BY id")).fetchall()
        for row in rows:
            connection.execute(text("UPDATE leads SET lead_code = :code WHERE id = :id"), {"code": format_lead_code(row.id), "id": row.id})

def assign_lead_code(db, lead) -> None:
    """Assign a stable public lead code after a lead receives its numeric id."""
    if lead.id and not lead.lead_code:
        lead.lead_code = format_lead_code(lead.id)
        db.add(lead)
        db.commit()
        db.refresh(lead)

def assign_missing_lead_codes(db) -> None:
    from .models import Lead
    leads = db.query(Lead).filter((Lead.lead_code.is_(None)) | (Lead.lead_code == "")).order_by(Lead.id).all()
    for lead in leads:
        lead.lead_code = format_lead_code(lead.id)
        db.add(lead)
    if leads:
        db.commit()

def init_db() -> None:
    """Import models and create/migrate database tables if they do not exist yet."""
    global _initialized
    from . import models  # noqa: F401 - registers SQLAlchemy models on metadata
    Base.metadata.create_all(bind=engine)
    ensure_lead_code_column()

_initialized = False

def init_db() -> None:
    """Import models and create database tables if they do not exist yet."""
    global _initialized
    from . import models  # noqa: F401 - registers SQLAlchemy models on metadata
    Base.metadata.create_all(bind=engine)


    _initialized = True

def get_db():
    if not _initialized:
        init_db()

def get_db():



    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from . import models
    Base.metadata.create_all(bind=engine)


