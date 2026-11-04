"""建立市場基本資料與示意股票清單。python -m app.seed"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as M
from app.db.session import init_db, session_scope
from app.providers import get_provider
from app.workers.pipeline import sync_instruments

MARKETS = [
    ("TW", "XTAI", "Asia/Taipei", "TWD", "13:30"),
    ("US", "XNAS", "America/New_York", "USD", "16:00"),
    ("JP", "XTKS", "Asia/Tokyo", "JPY", "15:30"),
]


def seed(db: Session, admin_email: str | None = None) -> None:
    for mid, mic, tz, ccy, close in MARKETS:
        if db.get(M.Market, mid) is None:
            db.add(M.Market(id=mid, mic=mic, timezone=tz, currency=ccy, close_time=close))
    db.flush()
    for mid, *_ in MARKETS:
        sync_instruments(db, get_provider(mid), mid)
    if admin_email and db.scalar(select(M.User).where(M.User.email == admin_email)) is None:
        db.add(M.User(email=admin_email, display_name="管理員", role="admin"))


if __name__ == "__main__":
    import os
    init_db()
    with session_scope() as db:
        seed(db, os.getenv("ADMIN_EMAIL"))
    print("已建立市場與示意股票。")
