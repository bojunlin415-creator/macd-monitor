from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.models import Base


def make_engine(url: str | None = None):
    url = url or settings.database_url
    kw = {"future": True}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False}
    if url.startswith("postgresql"):
        from sqlalchemy.pool import NullPool
        kw["poolclass"] = NullPool  # 無伺服器環境：每次請求自行連線，交給 Neon／Supabase 連線池
    eng = create_engine(url, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _fk(conn, _):  # noqa: ANN001
            conn.execute("PRAGMA foreign_keys=ON")
    return eng


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(eng=None) -> None:
    Base.metadata.create_all(eng or engine)


@contextmanager
def session_scope(factory=None) -> Iterator[Session]:
    s = (factory or SessionLocal)()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
