from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings



class Base(DeclarativeBase):
    pass


def _connect_args(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


from sqlalchemy import create_engine

from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import get_settings

settings = get_settings()
database_url = settings.database_url

engine = create_engine(
    database_url,
    connect_args=_connect_args(database_url),
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)

_initialized = False


def format_lead_code(lead_id: int) -> str:
    return f"BLF-{lead_id:06d}"


def ensure_lead_code_column() -> None:
    if not engine.url.get_backend_name().startswith("sqlite"):
        return

    with engine.begin() as connection:
        table_exists = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='leads'"
        ).first()

        if not table_exists:
            return

        columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(leads)"
            ).fetchall()
        }

        if "lead_code" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE leads "
                "ADD COLUMN lead_code VARCHAR(20)"
            )

        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "ix_leads_lead_code ON leads (lead_code)"
        )

        rows = connection.execute(
            text(
                "SELECT id FROM leads "
                "WHERE lead_code IS NULL OR lead_code = '' "
                "ORDER BY id"
            )
        ).fetchall()

        for row in rows:
            lead_id = row[0]
            connection.execute(
                text(
                    "UPDATE leads "
                    "SET lead_code = :code "
                    "WHERE id = :id"
                ),
                {
                    "code": format_lead_code(lead_id),
                    "id": lead_id,
                },
            )


def assign_lead_code(db, lead) -> None:
    if lead.id and not lead.lead_code:
        lead.lead_code = format_lead_code(lead.id)
        db.add(lead)
        db.commit()
        db.refresh(lead)


def assign_missing_lead_codes(db) -> None:
    from .models import Lead

    leads = (
        db.query(Lead)
        .filter(
            (Lead.lead_code.is_(None))
            | (Lead.lead_code == "")
        )
        .order_by(Lead.id)
        .all()
    )

    for lead in leads:
        lead.lead_code = format_lead_code(lead.id)
        db.add(lead)

    if leads:
        db.commit()


def init_db() -> None:
    global _initialized


    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_lead_code_column()

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


