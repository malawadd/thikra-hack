"""SQLAlchemy 2 persistence with SQLite locally and PostgreSQL in deployment."""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _connect_args() -> dict[str, bool]:
    return {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}


engine = create_engine(settings.database_url, connect_args=_connect_args(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def initialize_database() -> None:
    from app.commerce import models as commerce_models  # noqa: F401
    from app.thikra import models  # noqa: F401

    if settings.database_url.startswith("sqlite:///./"):
        Path(settings.database_url.removeprefix("sqlite:///./")).parent.mkdir(
            parents=True, exist_ok=True
        )
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
