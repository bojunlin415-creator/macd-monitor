"""每日盤後資料管線（規格 3.1、3.4）。

擷取 → 品質檢核 → 復權 → 週線 → 指標 → 規則判斷 → 事件狀態 → 待送佇列（同一交易）。
LINE 投遞在交易外由 dispatcher 執行。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import models as M
from app.engine import indicators as I
from app.engine import signals as S
from app.engine import state as ST
from app.engine.weekly import (
    CorporateAction, DailyBar, QualityError, Session as CalSession, adjust_for_actions,
    aggregate_weekly, rvol_series, week_key, WEEK_CONFIRMED, WEEK_PROVISIONAL,
)
from app.providers.base import MarketDataProvider, ProviderError

log = logging.getLogger(__name__)

HISTORY_YEARS = 10
INGEST_BUDGET_S = 200  # Vercel 函式上限 300 秒，保留時間給計算與推播


# ---- 擷取 ---------------------------------------------------------------
def sync_instruments(db: Session, provider: MarketDataProvider, market: str) -> None:
    """依行情來源更新股票清單；換了來源時，舊來源的股票標記下市，不再出現在搜尋與計算中。"""
    listed = set()
    for info in provider.instruments(market):
        listed.add(info.symbol)
        inst = db.scalar(select(M.Instrument).where(M.Instrument.mic == info.mic, M.Instrument.symbol == info.symbol))
        if inst is None:
            db.add(M.Instrument(market_id=market, mic=info.mic, symbol=info.symbol, name=info.name,
                                currency=info.currency, lot_size=info.lot_size, license_status=provider.source))
        elif inst.delisted_on is not None and inst.license_status == provider.source:
            inst.delisted_on = None
    open_universe = hasattr(provider, "make_instrument")  # 可接受清單外代號（使用者匯入的股票）
    for inst in db.scalars(select(M.Instrument).where(M.Instrument.market_id == market, M.Instrument.delisted_on.is_(None))):
        if inst.license_status == provider.source:
            continue
        if inst.license_status == "sample":
            if inst.symbol not in listed:
                inst.delisted_on = date.today()
        elif open_universe:
            # 換成另一個真實行情來源：保留股票與自選，舊來源的日線刪掉，下次作業由新來源重新回補
            db.execute(delete(M.DailyBarRow).where(M.DailyBarRow.instrument_id == inst.id))
            db.execute(delete(M.CorporateActionRow).where(M.CorporateActionRow.instrument_id == inst.id))
            inst.license_status = provider.source
            inst.state = "backfilling"
        # 其餘情況（金鑰暫時沒設、退回示意資料）不動真實來源的股票，避免使用者的自選被下架


def ingest_market(db: Session, provider: MarketDataProvider, market: str, as_of: date,
                  instrument_ids: Optional[set[str]] = None) -> dict:
    """抓取日曆、日 K 與公司行動；以來源鍵冪等匯入。"""
    start = as_of - timedelta(days=365 * HISTORY_YEARS + 3)
    end = as_of + timedelta(days=7)
    cal = {s.local_date: s for s in provider.sessions(market, start, end)}
    for row in db.scalars(select(M.MarketSession).where(M.MarketSession.market_id == market,
                                                        M.MarketSession.local_date >= start,
                                                        M.MarketSession.local_date <= end)):
        if row.local_date not in cal:  # 來源日曆更正（例如補上休市日）
            db.delete(row)
        else:
            cal.pop(row.local_date)
    for s in cal.values():
        db.add(M.MarketSession(market_id=market, local_date=s.local_date, weight=s.weight))

    q = select(M.Instrument).where(M.Instrument.market_id == market, M.Instrument.delisted_on.is_(None))
    insts = [i for i in db.scalars(q) if instrument_ids is None or i.id in instrument_ids]
    n_bars, errors, deferred = 0, [], 0
    t0 = time.monotonic()
    for inst in insts:
        last = db.scalar(select(M.DailyBarRow.date).where(M.DailyBarRow.instrument_id == inst.id)
                         .order_by(M.DailyBarRow.date.desc()).limit(1))
        fetch_from = (last + timedelta(days=1)) if last else start
        if last is None:
            inst.state = "backfilling"
            if time.monotonic() - t0 > INGEST_BUDGET_S:  # 回補較慢，超過時間預算留待下次排程
                deferred += 1
                continue
        try:
            fetched = provider.daily_bars(market, inst.symbol, fetch_from, as_of)
        except ProviderError as e:  # 單一標的失敗（無權限、查無代號）不影響其他股票
            inst.state = "unavailable"
            errors.append(f"{inst.symbol}: {e}")
            continue
        for b in fetched:
            db.add(M.DailyBarRow(instrument_id=inst.id, date=b.date, source=provider.source, revision=1,
                                 open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume))
            n_bars += 1
        have = {(a.effective_date, a.type) for a in db.scalars(select(M.CorporateActionRow).where(M.CorporateActionRow.instrument_id == inst.id))}
        for a in provider.corporate_actions(market, inst.symbol):
            if (a.effective_date, a.kind) not in have:
                db.add(M.CorporateActionRow(instrument_id=inst.id, effective_date=a.effective_date, type=a.kind,
                                            price_factor=a.price_factor, volume_factor=a.volume_factor))
    mk = db.get(M.Market, market)
    mk.data_cutoff = as_of
    mk.last_success_at = datetime.now(timezone.utc)
    mk.data_state = "ok"
    db.flush()
    return {"instruments": len(insts), "bars": n_bars, "errors": errors, "deferred": deferred}


# ---- 計算 ---------------------------------------------------------------
@dataclass
class Computation:
    weeks: list
    closes: list[float]
    macd: I.MacdSeries
    ma: dict[int, list]
    rvol: list
    params: I.MacdParams
    input_hash: str
    raw_last_close: Optional[float]

    @property
    def t(self) -> int:
        return len(self.weeks) - 1

    def warmup_index(self) -> int:
        return self.params.warmup_weeks - 1


def load_series(db: Session, inst: M.Instrument, as_of: date) -> tuple[list[DailyBar], list[CorporateAction], list[CalSession]]:
    rows = db.scalars(select(M.DailyBarRow).where(M.DailyBarRow.instrument_id == inst.id, M.DailyBarRow.date <= as_of)
                      .order_by(M.DailyBarRow.date, M.DailyBarRow.revision)).all()
    latest: dict[date, M.DailyBarRow] = {}
    for r in rows:  # 每日取最新修訂版
        latest[r.date] = r
    bars = [DailyBar(r.date, r.open, r.high, r.low, r.close, r.volume) for r in latest.values()]
    acts = [CorporateAction(a.effective_date, a.price_factor, a.type) for a in
            db.scalars(select(M.CorporateActionRow).where(M.CorporateActionRow.instrument_id == inst.id))]
    sess = [CalSession(s.local_date, s.weight) for s in
            db.scalars(select(M.MarketSession).where(M.MarketSession.market_id == inst.market_id,
                                                     M.MarketSession.local_date <= as_of + timedelta(days=7)))]
    return bars, acts, sess


def compute(bars, acts, sess, as_of: date, params: I.MacdParams, final_through: Optional[date] = None) -> Computation:
    adj = adjust_for_actions(bars, acts)
    weeks = aggregate_weekly(adj, sess, as_of=as_of, final_through=final_through)
    closes = [w.close for w in weeks]
    h = hashlib.sha256(json.dumps([(str(w.week_key), w.close, w.volume, w.status) for w in weeks[-200:]]).encode()).hexdigest()
    return Computation(
        weeks=weeks, closes=closes, macd=I.macd(closes, params), ma=I.moving_averages(closes),
        rvol=rvol_series(weeks), params=params, input_hash=h,
        raw_last_close=bars[-1].close if bars else None,
    )


def snapshot_data(c: Computation, t: int) -> dict:
    def at(seq, i):
        return seq[i] if 0 <= i < len(seq) else None
    w = c.weeks[t]
    return {
        "week_key": str(w.week_key), "week_status": w.status,
        "elapsed": w.elapsed_count, "expected": w.expected_count,
        "close": at(c.closes, t), "close_prev": at(c.closes, t - 1),
        "dif": at(c.macd.dif, t), "dea": at(c.macd.dea, t), "hist": at(c.macd.hist, t),
        "dif_prev": at(c.macd.dif, t - 1), "dea_prev": at(c.macd.dea, t - 1), "hist_prev": at(c.macd.hist, t - 1),
        "ma": {str(n): at(v, t) for n, v in c.ma.items()},
        "ma20_prev": at(c.ma[20], t - 1),
        "rvol": at(c.rvol, t), "eps": I.eps_at(at(c.closes, t - 1)),
        "params": [c.params.fast, c.params.slow, c.params.signal],
        "weeks_available": len(c.weeks), "warmup_weeks": c.params.warmup_weeks,
    }


def param_hash(p: I.MacdParams, rvol_up: float) -> str:
    return f"{p.fast}-{p.slow}-{p.signal}-{rvol_up:.2f}"


# ---- 規則 ---------------------------------------------------------------
def effective_rule(db: Session, user_id: str, instrument_id: str, wk: date) -> Optional[M.RuleVersion]:
    """個股覆寫優先於全域；規則異動於下一交易週生效。"""
    for inst_filter in (M.RuleVersion.instrument_id == instrument_id, M.RuleVersion.instrument_id.is_(None)):
        r = db.scalar(select(M.RuleVersion).where(M.RuleVersion.user_id == user_id, inst_filter,
                                                  M.RuleVersion.effective_week <= wk)
                      .order_by(M.RuleVersion.effective_week.desc(), M.RuleVersion.version.desc()).limit(1))
        if r:
            return r
    return None


# ---- 判斷與事件 -----------------------------------------------------------
def _notify_allowed(db: Session, user: M.User, code: str, as_of: date) -> bool:
    if user.status != "active":
        return False
    binding = db.get(M.LineBinding, user.id)
    if binding is None or binding.status != "active":
        return False
    prefs = db.get(M.NotificationPrefs, user.id)
    if prefs:
        if prefs.paused_until and as_of <= prefs.paused_until:
            return False
        if code in (prefs.muted_codes or []) and code not in S.MANDATORY_CODES:
            return False
    return True


def apply_evaluation(db: Session, *, user: M.User, inst: M.Instrument, watch: M.WatchlistItem,
                     comp: Computation, t: int, phase: str, rule: Optional[M.RuleVersion],
                     thresholds: S.Thresholds, batch_id: str, as_of: date) -> list[str]:
    """把第 t 週的判斷寫入事件表與待送佇列；回傳新排入的 outbox id。"""
    wk = comp.weeks[t].week_key
    ev = S.classify_week(t, comp.macd.hist, comp.closes, comp.ma[20], comp.rvol, thresholds,
                         warmup_index=comp.warmup_index())
    if not ev.evaluable:
        return []
    pkey = param_hash(comp.params, thresholds.rvol_up)
    snap = db.scalar(select(M.IndicatorSnapshot).where(
        M.IndicatorSnapshot.instrument_id == inst.id, M.IndicatorSnapshot.week_key == wk,
        M.IndicatorSnapshot.param_hash == pkey, M.IndicatorSnapshot.input_hash == comp.input_hash))
    if snap is None:
        snap = M.IndicatorSnapshot(instrument_id=inst.id, week_key=wk, param_hash=pkey, input_hash=comp.input_hash,
                                   engine_version=I.ENGINE_VERSION, data=snapshot_data(comp, t))
        db.add(snap)
        db.flush()
    reason = {
        "conditions": [c.__dict__ for c in ev.conditions],
        "codes": ev.codes, "eps": ev.eps,
    }
    queued: list[str] = []
    silent = watch.baseline_week is not None and wk <= watch.baseline_week  # 新加入：先建立狀態
    for code in S.PRIORITY:
        holds = code in ev.codes
        event = db.scalar(select(M.SignalEvent).where(
            M.SignalEvent.user_id == user.id, M.SignalEvent.instrument_id == inst.id,
            M.SignalEvent.week_key == wk, M.SignalEvent.code == code))
        tr = ST.transition(event.status if event else None, holds, phase)
        if not tr.changed:
            if event and holds:
                event.snapshot_id, event.reason_json = snap.id, reason
            continue
        level, direction = S.SIGNAL_DEFS[code]
        if event is None:
            event = M.SignalEvent(user_id=user.id, instrument_id=inst.id, week_key=wk, code=code, level=level,
                                  direction=direction, status=tr.status, rule_version_id=rule.id if rule else None,
                                  snapshot_id=snap.id, reason_json=reason)
            db.add(event)
            db.flush()
            prev = None
        else:
            prev = event.status
            event.status = tr.status
            if holds:
                event.snapshot_id, event.reason_json = snap.id, reason
        db.add(M.SignalRevision(event_id=event.id, from_status=prev, to_status=tr.status, input_hash=comp.input_hash))
        if tr.notify and not silent and _notify_allowed(db, user, code, as_of):
            key = ST.dedupe_key(user.id, inst.id, str(wk), code, tr.notify, event.data_revision)
            if db.scalar(select(M.NotificationOutbox.id).where(M.NotificationOutbox.dedupe_key == key)) is None:
                ob = M.NotificationOutbox(user_id=user.id, event_id=event.id, kind=tr.notify, dedupe_key=key,
                                          payload_snapshot=_payload(inst, event, snap, ev), batch_id=batch_id,
                                          market_id=inst.market_id)
                db.add(ob)
                db.flush()
                queued.append(ob.id)
    # 主標籤：同週有效代碼中優先序最高者
    active = db.scalars(select(M.SignalEvent).where(
        M.SignalEvent.user_id == user.id, M.SignalEvent.instrument_id == inst.id, M.SignalEvent.week_key == wk)).all()
    live = [e for e in active if e.status in (ST.PROVISIONAL, ST.CONFIRMED)]
    top = S.sort_by_priority([e.code for e in live])[0] if live else None
    for e in active:
        e.is_primary = e.code == top if top else e.code == S.sort_by_priority([x.code for x in active])[0]
    return queued


def _payload(inst: M.Instrument, event: M.SignalEvent, snap: M.IndicatorSnapshot, ev: S.WeekEvaluation) -> dict:
    d = snap.data
    wk = date.fromisoformat(d["week_key"])
    return {
        "market": inst.market_id, "symbol": inst.symbol, "name": inst.name, "code": event.code,
        "status": event.status, "close": d["close"],
        "week_change": (d["close"] / d["close_prev"] - 1) if d.get("close_prev") else None,
        "h_prev": d["hist_prev"], "h": d["hist"], "rvol": d["rvol"], "dif": d["dif"], "dea": d["dea"],
        "params": d["params"], "elapsed": d["elapsed"], "expected": d["expected"],
        "week_no": wk.isocalendar()[1], "event_id": event.id, "instrument_id": inst.id,
        "conditions": [{"key": c.key, "state": c.state} for c in ev.conditions
                       if c.key in ("pos_shrink", "neg_shrink", "golden_cross", "death_cross", "volume_down_price", "break_ma20", "volume_up", "above_ma20")
                       and (c.state == S.MET or c.key in _RELEVANT.get(event.code, ()))],
    }


_RELEVANT = {
    "POS_WEAK": ("pos_shrink", "volume_down_price", "break_ma20"),
    "RISK": ("pos_shrink", "volume_down_price", "break_ma20"),
    "EXIT": ("death_cross", "volume_down_price", "break_ma20"),
    "NEG_WEAK": ("neg_shrink",),
    "CROSS": ("golden_cross", "above_ma20", "volume_up"),
    "ENTRY": ("golden_cross", "above_ma20", "volume_up"),
}


def evaluate_market(db: Session, provider: MarketDataProvider, market: str, as_of: date, batch_id: str) -> dict:
    """對市場內所有被監測的股票判斷暫定週與最近確認週。"""
    final_through = provider.final_through(market, as_of)
    watches = db.scalars(select(M.WatchlistItem).join(M.Instrument).where(
        M.Instrument.market_id == market, M.Instrument.delisted_on.is_(None))).all()
    by_inst: dict[str, list[M.WatchlistItem]] = {}
    for w in watches:
        by_inst.setdefault(w.instrument_id, []).append(w)
    stats = {"instruments": 0, "queued": 0, "quality_failed": 0}
    for inst_id, ws in by_inst.items():
        inst = db.get(M.Instrument, inst_id)
        bars, acts, sess = load_series(db, inst, as_of)
        if not bars:
            if inst.state != "unavailable":  # 來源拒絕（無權限、查無代號）時保留原因，不顯示成準備中
                inst.state = "backfilling"
            continue
        cache: dict[str, Computation] = {}
        try:
            default = compute(bars, acts, sess, as_of, I.MacdParams(), final_through)
        except QualityError as e:
            inst.state = "quality_failed"
            stats["quality_failed"] += 1
            log.warning("資料品質異常，隔離 %s：%s", inst.symbol, e)
            continue
        inst.weeks_available = len(default.weeks)
        inst.state = "ready" if I.warmup_ok(default.t) else "warmup"
        stats["instruments"] += 1
        for w in ws:
            user = db.get(M.User, w.user_id)
            if user.status != "active":
                continue
            last_wk = default.weeks[-1].week_key
            rule = effective_rule(db, user.id, inst.id, last_wk)
            params = I.MacdParams(rule.fast, rule.slow, rule.signal) if rule else I.MacdParams()
            th = S.Thresholds(rvol_up=rule.rvol_up) if rule else S.Thresholds()
            pk = param_hash(params, th.rvol_up)
            comp = cache.get(pk)
            if comp is None:
                comp = default if params == default.params else compute(bars, acts, sess, as_of, params, final_through)
                cache[pk] = comp
            t = comp.t
            status = comp.weeks[t].status
            # 若前一週已確認，先做（或重做，具冪等）週末結論
            if t >= 1 and comp.weeks[t - 1].status == WEEK_CONFIRMED and status != WEEK_CONFIRMED:
                stats["queued"] += len(apply_evaluation(db, user=user, inst=inst, watch=w, comp=comp, t=t - 1,
                                                        phase=ST.PHASE_WEEKEND, rule=rule, thresholds=th,
                                                        batch_id=batch_id, as_of=as_of))
            if status == WEEK_PROVISIONAL:
                phase = ST.PHASE_INTRADAY
            elif status == WEEK_CONFIRMED:
                phase = ST.PHASE_WEEKEND
            else:
                continue  # 待確認：維持現狀
            stats["queued"] += len(apply_evaluation(db, user=user, inst=inst, watch=w, comp=comp, t=t, phase=phase,
                                                    rule=rule, thresholds=th, batch_id=batch_id, as_of=as_of))
    return stats


def run_market(db: Session, provider: MarketDataProvider, market: str, as_of: date, version: str = "1") -> M.Job:
    """一個市場的盤後作業；工作鍵含 market／date／job_type／version 防止重入。"""
    key = f"{market}|{as_of}|after_close|{version}"
    job = db.scalar(select(M.Job).where(M.Job.job_key == key))
    if job and job.status == "done":
        return job
    if job is None:
        job = M.Job(job_key=key, job_type="after_close", market_id=market)
        db.add(job)
    job.status = "running"
    db.flush()
    try:
        with db.begin_nested():  # 失敗時整批回滾，事件與待送佇列不會只寫一半
            watched = {w.instrument_id for w in db.scalars(select(M.WatchlistItem))}
            ing = ingest_market(db, provider, market, as_of, watched)
            ev = evaluate_market(db, provider, market, as_of, batch_id=key)
        job.detail = {"ingest": ing, "evaluate": ev}
        job.status = "done"
        job.progress = 1.0
    except Exception as e:  # 單一市場故障不阻斷其他市場
        job.status = "failed"
        job.error = f"{type(e).__name__}: {e}"
        mk = db.get(M.Market, market)
        mk.data_state = "error"
        log.exception("市場作業失敗 %s", key)
    job.heartbeat_at = datetime.now(timezone.utc)
    ing = (job.detail or {}).get("ingest", {})
    log.info("盤後作業 %s 來源=%s 狀態=%s 股票=%s 日線=%s 失敗=%s 延後=%s 錯誤=%s",
             key, getattr(provider, "source", "?"), job.status, ing.get("instruments"), ing.get("bars"),
             len(ing.get("errors") or []), ing.get("deferred"), (ing.get("errors") or [])[:3] or job.error)
    return job


def current_week_key(d: date) -> date:
    return week_key(d)
