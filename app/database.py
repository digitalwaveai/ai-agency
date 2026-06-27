from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _connect_args(url: str) -> dict:
    return {"check_same_thread": False} if url.startswith("sqlite") else {}


_database_url = get_settings().database_url
engine = create_engine(
    _database_url,
    connect_args=_connect_args(_database_url),
)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)
_initialized = False


def init_db() -> None:
    """Create tables and safely seed the built-in commercial plans."""
    global _initialized

    from . import models  # noqa: F401 - registers SQLAlchemy models

    Base.metadata.create_all(bind=engine)

    from .services.plan_service import seed_default_plans

    db = SessionLocal()
    try:
        seed_default_plans(db)
    finally:
        db.close()

    _initialized = True


def get_db():
    if not _initialized:
        init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
