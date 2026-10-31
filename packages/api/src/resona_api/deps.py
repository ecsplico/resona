"""Shared FastAPI dependencies."""

from collections.abc import Iterator

from sqlmodel import Session

from .db.engine import engine


def get_db_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
