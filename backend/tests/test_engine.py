"""引擎固定樣本：對應規格第 12 章 UAT。"""
from datetime import date, timedelta
import math
import random

import pytest

from app.engine import indicators as I
from app.engine import signals as S
from app.engine import state as ST
from app.engine.positions import BUY, SELL, SPLIT, OversellError, Transaction, replay
from app.engine.weekly import (
    CorporateAction, DailyBar, QualityError, adjust_for_actions, aggregate_weekly,
    rvol_series, weekday_sessions, WEEK_CONFIRMED, WEEK_PENDING, WEEK_PROVISIONAL,
)


# ---- UAT-01 週線聚合 ------------------------------------------------------
def test_uat01_weekly_aggregation_keeps_raw():
    mon = date(2026, 11, 2)
    raw = [DailyBar(mon, 10, 12, 9, 11, 100), DailyBar(mon + timedelta(1), 11, 13, 10, 12, 200)]
    sessions = weekday_sessions(mon, mon + timedelta(1))
    wk = aggregate_weekly(raw, sessions, as_of=mon + timedelta(1))[0]
    assert (wk.open, wk.high, wk.low, wk.close, wk.volume) == (10, 13, 9, 12, 300)
    assert raw[0].close == 11  # 原始資料保留


def test_quality_gate_rejects_inverted_bar():
    with pytest.raises(QualityError):
        aggregate_weekly([DailyBar(date(2026, 11, 2), 10, 9, 11, 10, 1)], weekday_sessions(date(2026, 11, 2), date(2026, 11, 2)), date(2026, 11, 2))


# ---- UAT-02 拆股 ----------------------------------------------------------
def test_uat02_split_adjustment_and_position():
    d = date(2026, 1, 5)
    bars = [DailyBar(d, 100, 100, 100, 100, 100)]
    adj = adjust_for_actions(bars, [CorporateAction(date(2026, 2, 2), 0.5)])
    assert adj[0].close == 50 and adj[0].volume == 200
    assert bars[0].close == 100
    p = replay([Transaction(BUY, d, 10, 100, 0), Transaction(SPLIT, date(2026, 2, 2), split_ratio=2)])
    assert p.quantity == 20 and p.cost_basis == 1000


# ---- UAT-03 MACD 獨立公式比對 ---------------------------------------------
def _reference_macd(c, f=12, s=26, g=9):
    def ema_ref(xs, n):
        out = [None] * len(xs)
        k = 2 / (n + 1)
        idx = [i for i, x in enumerate(xs) if x is not None]
        s0 = idx[0]
        out[s0 + n - 1] = sum(xs[s0:s0 + n]) / n
        for i in range(s0 + n, len(xs)):
            out[i] = xs[i] * k + out[i - 1] * (1 - k)
        return out
    ef, es = ema_ref(c, f), ema_ref(c, s)
    dif = [None if (a is None or b is None) else a - b for a, b in zip(ef, es)]
    dea = ema_ref(dif, g)
    return dif, dea


def test_uat03_macd_matches_reference_and_warmup():
    rnd = random.Random(42)
    c = [100.0]
    for _ in range(199):
        c.append(c[-1] * (1 + rnd.uniform(-0.05, 0.05)))
    m = I.macd(c)
    dif, dea = _reference_macd(c)
    for i in range(200):
        if dea[i] is None:
            assert m.dea[i] is None
            continue
        tol = max(1e-8, 1e-10 * abs(dea[i]))
        assert abs(m.dif[i] - dif[i]) <= tol and abs(m.dea[i] - dea[i]) <= tol
    assert m.dif[24] is None and m.dif[25] is not None
    assert m.dea[32] is None and m.dea[33] is not None
    assert I.MacdParams().warmup_weeks == 104
    assert not I.warmup_ok(102) and I.warmup_ok(103)
    assert I.MacdParams(5, 10, 5).warmup_weeks == 60


def test_param_validation():
    with pytest.raises(ValueError, match="慢線週數必須大於快線週數"):
        I.MacdParams(26, 12, 9).validate()


def test_provisional_week_replaces_close_not_accumulates():
    """同週每日更新只替換暫定週 C，不累積五次 EMA。"""
    base = [100 + math.sin(i / 5) * 5 for i in range(60)]
    once = I.macd(base + [104.0])
    # 模擬週一到週五每日重算：每次都以完成週序列＋最新暫定收盤重算
    for daily_close in (101.0, 102.0, 103.0, 104.0):
        I.macd(base + [daily_close])
    again = I.macd(base + [104.0])
    assert once.hist[-1] == again.hist[-1]


# ---- 訊號測試工具：直接給柱體序列 ----------------------------------------
def classify(h, closes=None, ma20=None, rvol=None, t=None, **kw):
    n = len(h)
    closes = closes or [100.0] * n
    ma20 = ma20 or [None] * n
    rvol = rvol or [None] * n
    t = n - 1 if t is None else t
    return S.classify_week(t, h, closes, ma20, rvol, warmup_index=0, **kw)


# ---- UAT-04 容差 ---------------------------------------------------------
def test_uat04_tolerance():
    assert I.eps_at(100) == pytest.approx(0.0001)
    assert S.POS_WEAK not in classify([0.8, 1.0, 0.99995]).codes
    assert S.POS_WEAK in classify([0.8, 1.0, 0.9998]).codes


# ---- UAT-05 負柱首次縮短不需量價 -----------------------------------------
def test_uat05_neg_weak_without_volume():
    ev = classify([-1.2, -1.5, -1.0])
    assert ev.codes == [S.NEG_WEAK]
    assert ev.cond("volume_up").state == S.NO_DATA
    assert S.ENTRY not in ev.codes


# ---- UAT-06 進場候選 vs 金叉（觀察）-------------------------------------
@pytest.mark.parametrize("rv,expect", [(1.20, S.ENTRY), (1.19, S.CROSS)])
def test_uat06_entry_vs_cross(rv, expect):
    h = [-0.3, -0.1, 0.1]
    closes = [95.0, 98.0, 100.0]
    ma20 = [96.0, 97.0, 97.5]
    ev = classify(h, closes, ma20, [None, None, rv])
    assert expect in ev.codes and S.NEG_WEAK in ev.codes  # 負柱沒縮短就翻正：同週也是買進點
    assert S.SIGNAL_DEFS[S.CROSS] == (S.LEVEL_OBSERVE, S.DIR_BULL)


def test_cross_without_volume_data_does_not_upgrade():
    ev = classify([-0.3, -0.1, 0.1], [95.0, 98.0, 100.0], [96.0, 97.0, 97.5], [None] * 3)
    assert S.CROSS in ev.codes and S.ENTRY not in ev.codes


# ---- UAT-07 買方減弱 → 警示 → 出場候選 -----------------------------------
def test_uat07_pos_weak_then_risk_then_exit():
    # 第 2 週正柱首次縮短，無量價 → 注意
    ev = classify([0.8, 1.0, 0.9], [100, 101, 102], [90, 91, 92], [None, None, 1.0])
    assert ev.primary == S.POS_WEAK
    # 第 3 週（背景期內）放量下跌 → 警示
    ev = classify([0.8, 1.0, 0.9, 0.7], [100, 101, 102, 99], [90, 91, 92, 93], [None, None, 1.0, 1.5])
    assert ev.primary == S.RISK and S.EXIT not in ev.codes
    # 跌破 MA20（不放量）→ 警示
    ev = classify([0.8, 1.0, 0.9, 0.7], [100, 101, 102, 91], [90, 91, 92, 93], [None, None, 1.0, 0.9])
    assert ev.primary == S.RISK
    # 放量下跌且跌破 MA20 → 出場候選
    ev = classify([0.8, 1.0, 0.9, 0.7], [100, 101, 102, 91], [90, 91, 92, 93], [None, None, 1.0, 1.5])
    assert ev.primary == S.EXIT and S.RISK in ev.codes
    # 死叉 → 出場候選
    ev = classify([0.8, 1.0, 0.5, -0.2])
    assert ev.primary == S.EXIT


def test_priority_order():
    assert S.sort_by_priority([S.NEG_WEAK, S.CROSS, S.POS_WEAK, S.ENTRY, S.RISK, S.EXIT]) == S.PRIORITY


# ---- 補充：連續縮短只發一次 -------------------------------------------
def test_continuous_shrink_only_first_segment():
    h = [0.5, 1.2, 1.0, 0.8, 1.1, 0.9]
    fired = [t for t in range(2, len(h)) if S.POS_WEAK in classify(h, t=t).codes]
    assert fired == [2]  # 同一段正柱只發一次賣出點


def test_cross_without_shrink_fires_buy_and_sell():
    # 台積電 2026：正柱縮短後翻成一週負柱，4/6 那週直接翻正 → 買進點；之後正柱首次縮短 → 賣出點
    h = [1.0, 2.0, 1.5, 0.8, -0.3, 0.53, 1.2, 2.0, 1.8, -0.5]
    buys = [t for t in range(2, len(h)) if S.NEG_WEAK in classify(h, t=t).codes]
    sells = [t for t in range(2, len(h)) if S.POS_WEAK in classify(h, t=t).codes]
    assert buys == [5] and sells == [2, 8]  # 第 8 週翻負前已發過賣出點，不重發


def test_death_cross_without_shrink_fires_sell():
    h = [0.5, 1.0, 1.5, -0.4, -0.8]
    assert [t for t in range(2, len(h)) if S.POS_WEAK in classify(h, t=t).codes] == [3]


def test_sell_fires_again_in_next_positive_segment():
    h = [0.5, 1.2, 1.0, -0.4, -0.9, -0.5, 0.6, 1.3, 1.1]
    sells = [t for t in range(2, len(h)) if S.POS_WEAK in classify(h, t=t).codes]
    buys = [t for t in range(2, len(h)) if S.NEG_WEAK in classify(h, t=t).codes]
    assert sells == [2, 8] and buys == [5]


def test_cross_zero_is_not_shrink():
    ev = classify([-0.5, -0.2, 0.3])
    assert ev.cond("neg_shrink").state == S.NOT_MET  # 翻正不算負柱縮短（買進點由翻正補發）


def test_warmup_blocks_signals():
    ev = S.classify_week(2, [0.8, 1.0, 0.9], [100] * 3, [None] * 3, [None] * 3, warmup_index=2)
    assert not ev.evaluable and ev.reason == "warmup"


# ---- UAT-08 同週反覆 + 重跑 ---------------------------------------------
def test_uat08_flapping_and_rerun_notifies_once():
    sent = set()
    status = None
    for holds in (True, False, True, True, True):  # 反覆 + 重跑
        tr = ST.transition(status, holds, ST.PHASE_INTRADAY)
        status = tr.status
        if tr.notify:
            sent.add(ST.dedupe_key("u", "i", "2026-11-02", "POS_WEAK", tr.notify))
    assert len(sent) == 1 and status == ST.PROVISIONAL
    for _ in range(3):
        tr = ST.transition(status, False, ST.PHASE_WEEKEND)
        status = tr.status
        if tr.notify:
            sent.add(ST.dedupe_key("u", "i", "2026-11-02", "POS_WEAK", tr.notify))
    assert status == ST.INVALIDATED and len(sent) == 2


def test_weekend_first_creates_confirmed_directly():
    tr = ST.transition(None, True, ST.PHASE_WEEKEND)
    assert tr.status == ST.CONFIRMED and tr.notify == ST.KIND_CONFIRM


def test_correction_after_confirm():
    tr = ST.transition(ST.CONFIRMED, False, ST.PHASE_REVISION)
    assert tr.status == ST.SUPERSEDED and tr.notify == ST.KIND_CORRECTION
    assert ST.dedupe_key("u", "i", "w", "X", "correction", 2).endswith("|2")


# ---- UAT-10 缺一天的週 ----------------------------------------------------
def test_uat10_missing_day_rvol_and_not_confirmed():
    start = date(2026, 1, 5)
    sessions = weekday_sessions(start, start + timedelta(weeks=20) - timedelta(days=1))
    bars = [DailyBar(s.local_date, 10, 10, 10, 10, 100) for s in sessions]
    # 最後一週只有週一、週二（週三缺），週已結束
    last_mon = start + timedelta(weeks=20)
    sessions += weekday_sessions(last_mon, last_mon + timedelta(days=4))
    bars += [DailyBar(last_mon, 10, 10, 10, 10, 100), DailyBar(last_mon + timedelta(1), 10, 10, 10, 10, 100)]
    weeks = aggregate_weekly(bars, sessions, as_of=last_mon + timedelta(days=4))
    assert weeks[-1].status == WEEK_PENDING
    assert weeks[-2].status == WEEK_CONFIRMED
    rv = rvol_series(weeks)
    assert rv[-1] == pytest.approx(1.0)  # 不誤比整週量為 0.4
    # 週未結束 → 暫定
    weeks2 = aggregate_weekly(bars, sessions, as_of=last_mon + timedelta(1))
    assert weeks2[-1].status == WEEK_PROVISIONAL


def test_early_close_weighting():
    mon = date(2026, 11, 23)
    sess = weekday_sessions(mon, mon + timedelta(4), holidays=[mon + timedelta(3)], early_closes={mon + timedelta(4): 3.5 / 6.5})
    assert sum(s.weight for s in sess) == pytest.approx(3 + 3.5 / 6.5)


# ---- UAT-15 持股成本 ------------------------------------------------------
def test_uat15_moving_average_cost():
    d = date(2026, 1, 5)
    p = replay([Transaction(BUY, d, 10, 100, 10), Transaction(BUY, d + timedelta(1), 10, 120, 10)])
    assert p.cost_basis == pytest.approx(2220) and p.avg_cost == pytest.approx(111)
    p.apply(Transaction(SELL, d + timedelta(2), 5, 130, 5))
    assert p.realized_pnl == pytest.approx(90)
    assert p.quantity == 15 and p.cost_basis == pytest.approx(1665)
    with pytest.raises(OversellError):
        p.apply(Transaction(SELL, d + timedelta(3), 16, 130, 0))
