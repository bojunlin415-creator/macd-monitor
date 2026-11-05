"""每週摘要（週六 12:00 台北時間）。唯一鍵 user_id + summary_week。"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as M
from app.engine.weekly import week_key
from app.notify.line_messages import weekly_summary_text


def queue_weekly_summaries(db: Session, today: date) -> int:
    wk = week_key(today)
    week_no = wk.isocalendar()[1]
    markets = {m.id: m for m in db.scalars(select(M.Market))}
    cutoffs = {mid: (m.data_cutoff if m.data_cutoff and week_key(m.data_cutoff) == wk else None) for mid, m in markets.items()}
    n = 0
    for b in db.scalars(select(M.LineBinding).where(M.LineBinding.status == "active")):
        user = db.get(M.User, b.user_id)
        prefs = db.get(M.NotificationPrefs, b.user_id)
        if user.status != "active" or (prefs and not prefs.weekly_summary):
            continue
        key = f"summary|{b.user_id}|{wk}"
        if db.scalar(select(M.NotificationOutbox.id).where(M.NotificationOutbox.dedupe_key == key)):
            continue
        evs = db.scalars(select(M.SignalEvent).where(M.SignalEvent.user_id == b.user_id, M.SignalEvent.week_key == wk,
                                                     M.SignalEvent.is_primary.is_(True))).all()
        confirmed = sum(1 for e in evs if e.status == "confirmed")
        invalid = sum(1 for e in evs if e.status == "invalidated")
        from app.api.signals import _holdings
        held = _holdings(db, b.user_id)
        risk = sum(1 for e in evs if e.instrument_id in held and e.code in ("POS_WEAK", "EXIT", "TREND_DOWN", "RISK") and e.status == "confirmed")
        missing = [f"{w.instrument.symbol} {w.instrument.name}" for w in
                   db.scalars(select(M.WatchlistItem).where(M.WatchlistItem.user_id == b.user_id))
                   if w.instrument.state == "quality_failed"]
        text = weekly_summary_text(week_no, confirmed, invalid, risk, cutoffs, missing)
        db.add(M.NotificationOutbox(user_id=b.user_id, kind="summary", dedupe_key=key, payload_snapshot={"text": text}))
        n += 1
    return n
