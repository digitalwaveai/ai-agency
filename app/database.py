from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _connect_args(url: str) -> dict:
    return {"check_same_thread": False} if url.startswith("sqlite") else {}


engine = create_engine(
    get_settings().database_url,
    connect_args=_connect_args(get_settings().database_url),
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

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

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()