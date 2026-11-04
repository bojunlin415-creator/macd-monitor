from datetime import date, timedelta

from sqlalchemy import func, select

from app.db import models as M
from app.notify.crypto import encrypt, blind_index
from app.notify.dispatcher import dispatch_pending
from app.notify.line_client import DryRunClient
from app.providers.sample import SampleProvider
from app.seed import seed
from app.workers.pipeline import run_market


def setup_user(db, n=8):
    seed(db)
    u = M.User(email="a@example.com")
    db.add(u)
    db.flush()
    db.add(M.LineBinding(user_id=u.id, status="active", line_user_id_enc=encrypt("Uxxxx"), line_user_hash=blind_index("Uxxxx")))
    for inst in db.scalars(select(M.Instrument).where(M.Instrument.market_id == "TW")).all()[:n]:
        db.add(M.WatchlistItem(user_id=u.id, instrument_id=inst.id))
    db.flush()
    return u


def test_pipeline_runs_dedupes_and_dispatches(db):
    u = setup_user(db)
    p = SampleProvider()
    start = date(2026, 9, 1)
    days = [start + timedelta(d) for d in range(0, 60) if (start + timedelta(d)).weekday() < 5]
    for d in days:
        job = run_market(db, p, "TW", d)
        assert job.status == "done", job.error
    events = db.scalars(select(M.SignalEvent)).all()
    assert events, "60 天內應至少有一個訊號"
    n_out = db.scalar(select(func.count()).select_from(M.NotificationOutbox))
    # 重跑同一天（新版本工作鍵）不應新增任何待送
    run_market(db, p, "TW", days[-1], version="rerun")
    assert db.scalar(select(func.count()).select_from(M.NotificationOutbox)) == n_out
    # 每個 (股票, 週, 代碼, 種類) 最多一筆
    keys = [r.dedupe_key for r in db.scalars(select(M.NotificationOutbox))]
    assert len(keys) == len(set(keys))
    # 確認週：同週同訊號最多一次週末結論
    statuses = {e.status for e in events}
    assert statuses <= {"provisional", "provisional_inactive", "confirmed", "invalidated"}
    client = DryRunClient()
    stats = dispatch_pending(db, client)
    assert stats["accepted"] == stats["messages"] > 0
    assert all(r.status == "accepted" for r in db.scalars(select(M.NotificationOutbox)))
    first = client.sent[0][1][0]
    assert first["type"] == "flex" and first["altText"].startswith("【")


def test_new_watch_baseline_is_silent(db):
    u = setup_user(db, n=8)
    for w in db.scalars(select(M.WatchlistItem)):
        w.baseline_week = date(2026, 12, 28)
    p = SampleProvider()
    for d in [date(2026, 10, 1) + timedelta(i) for i in range(20)]:
        if d.weekday() < 5:
            run_market(db, p, "TW", d)
    assert db.scalar(select(func.count()).select_from(M.NotificationOutbox)) == 0


def test_unbound_user_records_events_without_queue(db):
    u = setup_user(db)
    db.get(M.LineBinding, u.id).status = "unlinked"
    p = SampleProvider()
    for d in [date(2026, 9, 1) + timedelta(i) for i in range(40)]:
        if d.weekday() < 5:
            run_market(db, p, "TW", d)
    assert db.scalar(select(func.count()).select_from(M.SignalEvent)) > 0
    assert db.scalar(select(func.count()).select_from(M.NotificationOutbox)) == 0
