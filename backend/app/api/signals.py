"""訊號、總覽與個股週線圖。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models as M
from app.engine import indicators as I
from app.engine.signals import PRIORITY, SIGNAL_DEFS
from app.engine.weekly import week_key
from app.i18n import zh_tw as T
from app.api.deps import ApiError, current_user, get_db, ok
from app.api.me import WATCH_LIMIT, instrument_dict, _latest_rule
from app.workers.pipeline import compute, load_series

router = APIRouter(tags=["signals"])

LIVE = ("provisional", "confirmed")

UPGRADE_HINTS = {
    "NEG_WEAK": ("ENTRY", ["golden_cross", "above_ma20", "price_up", "volume_up"]),
    "CROSS": ("ENTRY", ["above_ma20", "price_up", "volume_up"]),
    "POS_WEAK": ("RISK", ["volume_down_price", "break_ma20"]),  # 任一
    "RISK": ("EXIT", ["death_cross", "volume_down_price", "break_ma20"]),
}


def _holdings(db: Session, user_id: str) -> set[str]:
    rows = db.execute(select(M.Transaction.instrument_id).where(M.Transaction.user_id == user_id,
                                                                M.Transaction.voided_by.is_(None)).distinct()).scalars()
    from app.api.positions import position_for  # 避免循環匯入
    return {iid for iid in rows if position_for(db, user_id, iid).quantity > 0}


def _also(db: Session, e: M.SignalEvent) -> list[str]:
    others = db.scalars(select(M.SignalEvent.code).where(
        M.SignalEvent.user_id == e.user_id, M.SignalEvent.instrument_id == e.instrument_id,
        M.SignalEvent.week_key == e.week_key, M.SignalEvent.id != e.id, M.SignalEvent.status.in_(LIVE))).all()
    return sorted(others, key=PRIORITY.index)


def event_dict(db: Session, e: M.SignalEvent, holdings: set[str]) -> dict:
    snap = e.snapshot.data if e.snapshot else {}
    close, prev = snap.get("close"), snap.get("close_prev")
    return {
        "id": e.id, "instrument": instrument_dict(e.instrument), "week_key": str(e.week_key),
        "week_no": e.week_key.isocalendar()[1], "year": e.week_key.isocalendar()[0],
        "code": e.code, "level": e.level, "direction": e.direction, "status": e.status,
        "is_primary": e.is_primary, "also": _also(db, e), "sentence": T.SIGNAL_SENTENCE[e.code],
        "rvol": snap.get("rvol"), "close": close,
        "week_change": (close / prev - 1) if close and prev else None,
        "elapsed": snap.get("elapsed"), "expected": snap.get("expected"),
        "held": e.instrument_id in holdings, "seen": e.seen,
        "first_at": e.first_at.isoformat(), "updated_at": e.updated_at.isoformat() if e.updated_at else None,
    }


def _market_status(db: Session) -> list[dict]:
    out = []
    for m in db.scalars(select(M.Market).order_by(M.Market.id)):
        out.append({"id": m.id, "data_cutoff": str(m.data_cutoff) if m.data_cutoff else None, "state": m.data_state,
                    "last_success_at": m.last_success_at.isoformat() if m.last_success_at else None})
    return out


@router.get("/signals")
def list_signals(request: Request, level: Optional[str] = None, direction: Optional[str] = None,
                 market: Optional[str] = None, status: Optional[str] = None, week: Optional[date] = None,
                 held: Optional[bool] = None, primary_only: bool = True, limit: int = 50, offset: int = 0,
                 user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    limit = max(1, min(limit, 200))
    stmt = select(M.SignalEvent).join(M.Instrument).where(M.SignalEvent.user_id == user.id)
    if primary_only:
        stmt = stmt.where(M.SignalEvent.is_primary.is_(True))
    if level:
        stmt = stmt.where(M.SignalEvent.level.in_(level.split(",")))
    if direction:
        stmt = stmt.where(M.SignalEvent.direction == direction)
    if market:
        stmt = stmt.where(M.Instrument.market_id.in_(market.split(",")))
    if status:
        stmt = stmt.where(M.SignalEvent.status.in_(status.split(",")))
    if week:
        stmt = stmt.where(M.SignalEvent.week_key == week_key(week))
    holdings = _holdings(db, user.id)
    if held is not None:
        stmt = stmt.where(M.SignalEvent.instrument_id.in_(holdings) if held else M.SignalEvent.instrument_id.not_in(holdings))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(M.SignalEvent.week_key.desc(), M.SignalEvent.first_at.desc()).offset(offset).limit(limit)).all()
    unread = db.scalar(select(func.count()).select_from(M.SignalEvent).where(
        M.SignalEvent.user_id == user.id, M.SignalEvent.is_primary.is_(True), M.SignalEvent.seen.is_(False)))
    return ok([event_dict(db, e, holdings) for e in rows], request, total=total, unread=unread,
              next_cursor=offset + limit if offset + limit < total else None)


def _get_event(db: Session, user: M.User, event_id: str) -> M.SignalEvent:
    e = db.get(M.SignalEvent, event_id)
    if e is None or e.user_id != user.id:
        raise ApiError(404, "not_found", "找不到這個頁面，或你沒有查看權限。")
    return e


@router.get("/signals/{event_id}")
def signal_detail(event_id: str, request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    e = _get_event(db, user, event_id)
    holdings = _holdings(db, user.id)
    d = event_dict(db, e, holdings)
    conds = (e.reason_json or {}).get("conditions", [])
    state = {c["key"]: c for c in conds}
    hint = None
    if e.code in UPGRADE_HINTS:
        target, keys = UPGRADE_HINTS[e.code]
        missing = [k for k in keys if state.get(k, {}).get("state") != "met"]
        hint = {"target": target, "missing": missing, "any": e.code in ("POS_WEAK",)}
    revs = db.scalars(select(M.SignalRevision).where(M.SignalRevision.event_id == e.id).order_by(M.SignalRevision.evaluated_at)).all()
    rule = db.get(M.RuleVersion, e.rule_version_id) if e.rule_version_id else None
    d.update({
        "conditions": conds, "eps": (e.reason_json or {}).get("eps"), "snapshot": e.snapshot.data if e.snapshot else None,
        "upgrade_hint": hint,
        "versions": {"engine": e.snapshot.engine_version if e.snapshot else None,
                     "input_hash": e.snapshot.input_hash[:12] if e.snapshot else None,
                     "rule_version": rule.version if rule else 0, "data_revision": e.data_revision},
        "history": [{"from": r.from_status, "to": r.to_status, "at": r.evaluated_at.isoformat()} for r in revs],
    })
    if not e.seen:
        e.seen = True
    return ok(d, request)


@router.get("/overview")
def overview(request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    holdings = _holdings(db, user.id)
    markets = _market_status(db)
    latest_week = {m["id"]: week_key(date.fromisoformat(m["data_cutoff"])) for m in markets if m["data_cutoff"]}
    base = select(M.SignalEvent).join(M.Instrument).where(M.SignalEvent.user_id == user.id, M.SignalEvent.is_primary.is_(True))
    # 今日待看：各市場最新一週、仍有效的主標籤事件
    today: list[M.SignalEvent] = []
    for mid, wk in latest_week.items():
        today += db.scalars(base.where(M.Instrument.market_id == mid, M.SignalEvent.week_key == wk,
                                       M.SignalEvent.status.in_(LIVE))).all()
    today.sort(key=lambda e: (e.seen, PRIORITY.index(e.code), -e.first_at.timestamp()))
    # 持股風險：注意／警示／出場候選
    risk_codes = ("POS_WEAK", "RISK", "EXIT")
    risk = []
    for iid in holdings:
        ev = db.scalars(base.where(M.SignalEvent.instrument_id == iid, M.SignalEvent.code.in_(risk_codes),
                                   M.SignalEvent.status.in_(LIVE)).order_by(M.SignalEvent.week_key.desc()).limit(1)).first()
        if ev and ev.week_key >= min(latest_week.values(), default=ev.week_key) - timedelta(days=7):
            risk.append(event_dict(db, ev, holdings))
    watched = db.scalars(select(M.WatchlistItem).where(M.WatchlistItem.user_id == user.id)).all()
    this_weeks = list(latest_week.values())
    counts = {"watched": len(watched), "limit": WATCH_LIMIT, "provisional": 0, "confirmed": 0,
              "unable": sum(1 for w in watched if w.instrument.state == "quality_failed"),
              "preparing": sum(1 for w in watched if w.instrument.state in ("backfilling", "warmup"))}
    if this_weeks:
        rows = db.execute(select(M.SignalEvent.status, func.count()).where(
            M.SignalEvent.user_id == user.id, M.SignalEvent.is_primary.is_(True),
            M.SignalEvent.week_key.in_(this_weeks)).group_by(M.SignalEvent.status)).all()
        for s, n in rows:
            if s in counts:
                counts[s] = n
    recent = db.scalars(base.order_by(M.SignalEvent.week_key.desc(), M.SignalEvent.first_at.desc()).limit(30)).all()
    b = db.get(M.LineBinding, user.id)
    p = db.get(M.NotificationPrefs, user.id)
    return ok({
        "markets": markets,
        "today": [event_dict(db, e, holdings) for e in today[:6]], "today_total": len(today),
        "holding_risk": risk, "holding_count": len(holdings),
        "week": counts,
        "events": [event_dict(db, e, holdings) for e in recent],
        "line_status": b.status if b else "none",
        "paused_until": str(p.paused_until) if p and p.paused_until and p.paused_until >= date.today() else None,
    }, request)


@router.get("/instruments/{instrument_id}")
def instrument_header(instrument_id: str, request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    inst = db.get(M.Instrument, instrument_id)
    if inst is None:
        raise ApiError(404, "not_found", "找不到這個頁面，或你沒有查看權限。")
    watch = db.scalar(select(M.WatchlistItem).where(M.WatchlistItem.user_id == user.id, M.WatchlistItem.instrument_id == inst.id))
    return ok({**instrument_dict(inst), "watch_id": watch.id if watch else None,
               "market_cutoff": str(db.get(M.Market, inst.market_id).data_cutoff or "")}, request)


@router.get("/instruments/{instrument_id}/weekly")
def weekly(instrument_id: str, request: Request, weeks: int = 156, basis: str = "adjusted",
           user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    inst = db.get(M.Instrument, instrument_id)
    if inst is None:
        raise ApiError(404, "not_found", "找不到這個頁面，或你沒有查看權限。")
    mk = db.get(M.Market, inst.market_id)
    as_of = mk.data_cutoff or date.today()
    bars, acts, sess = load_series(db, inst, as_of)
    if not bars:
        return ok({"weeks": [], "state": inst.state, "markers": []}, request)
    rule = _latest_rule(db, user.id, inst.id) or _latest_rule(db, user.id, None)
    params = I.MacdParams(rule.fast, rule.slow, rule.signal) if rule else I.MacdParams()
    c = compute(bars, acts, sess, as_of, params)
    raw_weeks = None
    if basis == "raw":
        from app.engine.weekly import aggregate_weekly
        raw_weeks = {w.week_key: w for w in aggregate_weekly(bars, sess, as_of)}
    n = len(c.weeks)
    start = max(0, n - max(10, min(weeks, 520)))
    out = []
    for i in range(start, n):
        w = c.weeks[i]
        src = raw_weeks.get(w.week_key, w) if raw_weeks else w
        out.append({
            "week_key": str(w.week_key), "status": w.status, "o": src.open, "h": src.high, "l": src.low, "c": src.close,
            "v": w.volume, "elapsed": w.elapsed_count, "expected": w.expected_count,
            "dif": c.macd.dif[i], "dea": c.macd.dea[i], "hist": c.macd.hist[i],
            "ma5": c.ma[5][i], "ma10": c.ma[10][i], "ma20": c.ma[20][i], "ma60": c.ma[60][i], "rvol": c.rvol[i],
        })
    evs = db.scalars(select(M.SignalEvent).where(M.SignalEvent.user_id == user.id, M.SignalEvent.instrument_id == inst.id,
                                                 M.SignalEvent.is_primary.is_(True))).all()
    markers = [{"id": e.id, "week_key": str(e.week_key), "code": e.code, "level": e.level, "direction": e.direction,
                "status": e.status} for e in evs]
    return ok({"weeks": out, "markers": markers, "params": [params.fast, params.slow, params.signal],
               "state": inst.state, "weeks_available": n, "warmup_weeks": params.warmup_weeks,
               "basis": basis, "raw_last_close": c.raw_last_close}, request)
