"""SQLAlchemy engine and session factory."""

from __future__ import annotations

from typing import Any, Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    connect_args=settings.sqlite_connect_args,
    pool_pre_ping=True,
    future=True,
)


if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
        """Put SQLite in WAL so the dashboard can read while a pass is writing.

        Under the default rollback journal a writer excludes readers outright, and a
        collection pass holds its write transaction for as long as its rate-limited
        fetches take — minutes. Every API request landing in that window would block
        until the pass committed, which reads to the user as a dead dashboard rather
        than a busy one. WAL lets readers continue against the last committed state,
        which is exactly what a snapshot-based UI should show: the previous complete
        observation, not a half-written one.

        Writers still serialise, so this is not a substitute for the single scheduler
        worker in ``services.scheduler`` — it addresses readers, which that does not.
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            # WAL alone still lets two writers collide; NORMAL keeps the fsync cost
            # off every commit, which matters when one pass commits per collector.
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
