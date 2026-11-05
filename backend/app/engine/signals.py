"""訊號分類：六種 MACD 週線訊號，分三個層次（2026-11-04 使用者定案）。

早期預警：B1 空方動能減弱（NEG_WEAK）、S1 多頭動能首次減弱（POS_WEAK）
交易候選：B2 黃金交叉（CROSS）、S2 死亡交叉（EXIT）
趨勢確認：B3 多頭趨勢確認（TREND_UP）、S3 空頭趨勢確認（TREND_DOWN）
ENTRY、RISK 為舊版代碼，只保留文案供歷史事件顯示，不再產生。
classify_week() 只看陣列，不碰資料庫；同一次判斷使用同一個 eps。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from .indicators import eps_at

# ---- 代碼、等級、方向 ----------------------------------------------------

NEG_WEAK = "NEG_WEAK"  # B1
CROSS = "CROSS"  # B2
TREND_UP = "TREND_UP"  # B3
POS_WEAK = "POS_WEAK"  # S1
EXIT = "EXIT"  # S2
TREND_DOWN = "TREND_DOWN"  # S3
RISK = "RISK"  # 舊版
ENTRY = "ENTRY"  # 舊版

LEVEL_OBSERVE = "observe"  # 早期預警
LEVEL_CANDIDATE = "candidate"  # 交易候選
LEVEL_TREND = "trend"  # 趨勢確認
LEVEL_ATTENTION = "attention"  # 舊版
LEVEL_WARNING = "warning"  # 舊版

DIR_BULL = "bullish"  # ↗ 偏多
DIR_BEAR = "bearish"  # ↘ 偏空

SIGNAL_DEFS: dict[str, tuple[str, str]] = {
    NEG_WEAK: (LEVEL_OBSERVE, DIR_BULL),
    CROSS: (LEVEL_CANDIDATE, DIR_BULL),
    TREND_UP: (LEVEL_TREND, DIR_BULL),
    POS_WEAK: (LEVEL_OBSERVE, DIR_BEAR),
    EXIT: (LEVEL_CANDIDATE, DIR_BEAR),
    TREND_DOWN: (LEVEL_TREND, DIR_BEAR),
    RISK: (LEVEL_WARNING, DIR_BEAR),
    ENTRY: (LEVEL_CANDIDATE, DIR_BULL),
}

# 主標籤優先序：交易候選 > 趨勢確認 > 早期預警（同層賣出優先）
PRIORITY = [EXIT, CROSS, TREND_DOWN, TREND_UP, POS_WEAK, NEG_WEAK, RISK, ENTRY]
LEVEL_ORDER = [LEVEL_CANDIDATE, LEVEL_TREND, LEVEL_OBSERVE, LEVEL_WARNING, LEVEL_ATTENTION]

# 六種訊號都可在通知設定關閉
MANDATORY_CODES: frozenset[str] = frozenset()


def sort_by_priority(codes: Sequence[str]) -> list[str]:
    return sorted(codes, key=PRIORITY.index)


# ---- 條件 ---------------------------------------------------------------

MET = "met"  # 已符合
NOT_MET = "not_met"  # 未符合
NO_DATA = "no_data"  # 資料不足


@dataclass
class Condition:
    key: str
    state: str
    actual: Optional[float] = None
    threshold: Optional[float] = None


@dataclass
class Thresholds:
    rvol_up: float = 1.20  # 放量門檻，使用者可調 1.00～3.00
    rvol_down: float = 0.80  # 縮量，本版固定

    def validate(self) -> None:
        if not (1.00 <= self.rvol_up <= 3.00):
            raise ValueError("放量門檻需介於 1.00～3.00。")


@dataclass
class WeekEvaluation:
    index: int
    eps: float
    codes: list[str] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)
    evaluable: bool = True
    reason: Optional[str] = None  # evaluable=False 時的原因

    @property
    def primary(self) -> Optional[str]:
        return sort_by_priority(self.codes)[0] if self.codes else None

    def cond(self, key: str) -> Optional[Condition]:
        return next((c for c in self.conditions if c.key == key), None)


def _gt(a: Optional[float], b: float) -> bool:
    return a is not None and a > b


def _lt(a: Optional[float], b: float) -> bool:
    return a is not None and a < b


def pos_shrink(h_prev: Optional[float], h: Optional[float], eps: float) -> bool:
    return _gt(h_prev, eps) and _gt(h, eps) and (h_prev - h) > eps  # type: ignore[operator]


def neg_shrink(h_prev: Optional[float], h: Optional[float], eps: float) -> bool:
    return _lt(h_prev, -eps) and _lt(h, -eps) and (h - h_prev) > eps  # type: ignore[operator]


def golden_cross(h_prev: Optional[float], h: Optional[float], eps: float) -> bool:
    return h_prev is not None and h is not None and h_prev <= eps and h > eps


def death_cross(h_prev: Optional[float], h: Optional[float], eps: float) -> bool:
    return h_prev is not None and h is not None and h_prev >= -eps and h < -eps


def _first(fn, hist: Sequence[Optional[float]], closes: Sequence[float], t: int) -> bool:
    """S[t] = true 且 S[t−1] = false 才是一段連續縮短的首次。"""
    if t < 1:
        return False
    eps_t = eps_at(closes[t - 1])
    s_t = fn(hist[t - 1], hist[t], eps_t)
    if not s_t:
        return False
    if t < 2:
        return False  # 無有效前態：只建基線，不補發
    eps_prev = eps_at(closes[t - 2])
    return not fn(hist[t - 2], hist[t - 1], eps_prev)


def _val(seq: Optional[Sequence[Optional[float]]], i: int) -> Optional[float]:
    return None if seq is None or i < 0 or i >= len(seq) else seq[i]


def _trend(hist, dif, dea, closes, ma20, i: int, up: bool) -> Optional[bool]:
    """B3／S3 狀態：DIF、DEA 同號，柱體連兩週朝同方向擴大，收盤在 20 週線同一側。"""
    if i < 2:
        return None
    h, h1, h2 = hist[i], hist[i - 1], hist[i - 2]
    d, e, c, m = _val(dif, i), _val(dea, i), closes[i], _val(ma20, i)
    if None in (h, h1, h2, d, e, m):
        return None
    eps = eps_at(closes[i - 1])
    if up:
        return d > eps and e > eps and h > eps and h - h1 > eps and h1 - h2 > eps and c > m + eps
    return d < -eps and e < -eps and h < -eps and h1 - h > eps and h2 - h1 > eps and c < m - eps


def classify_week(
    t: int,
    hist: Sequence[Optional[float]],
    closes: Sequence[float],
    ma20: Sequence[Optional[float]],
    rvol: Sequence[Optional[float]],
    thresholds: Thresholds = Thresholds(),
    warmup_index: int = 0,
    quality_ok: bool = True,
    dif: Optional[Sequence[Optional[float]]] = None,
    dea: Optional[Sequence[Optional[float]]] = None,
    ma10: Optional[Sequence[Optional[float]]] = None,
) -> WeekEvaluation:
    """判斷第 t 週（0 起算）成立哪些訊號。

    B1／S1／B3／S3 是「狀態」，只在由不成立轉為成立的那週發出；B2／S2 本身就是交叉事件。
    warmup_index：第一個暖機完成的週序（W − 1）。t 需大於它才有有效前態。
    """
    eps = eps_at(closes[t - 1] if t >= 1 else None)
    ev = WeekEvaluation(index=t, eps=eps)
    if not quality_ok:
        ev.evaluable = False
        ev.reason = "quality_failed"
        return ev
    if t <= warmup_index or t < 2:
        ev.evaluable = False
        ev.reason = "warmup"
        return ev
    h_prev, h = hist[t - 1], hist[t]
    if h is None or h_prev is None:
        ev.evaluable = False
        ev.reason = "warmup"
        return ev

    c_prev, c = closes[t - 1], closes[t]
    m, m_prev, m10 = ma20[t], ma20[t - 1], _val(ma10, t)
    d, d_prev = _val(dif, t), _val(dif, t - 1)
    rv = rvol[t]

    b1 = _first(neg_shrink, hist, closes, t)
    s1 = _first(pos_shrink, hist, closes, t)
    b2 = golden_cross(h_prev, h, eps)
    s2 = death_cross(h_prev, h, eps)
    up_now = _trend(hist, dif, dea, closes, ma20, t, True)
    dn_now = _trend(hist, dif, dea, closes, ma20, t, False)
    b3 = bool(up_now) and not _trend(hist, dif, dea, closes, ma20, t - 1, True)
    s3 = bool(dn_now) and not _trend(hist, dif, dea, closes, ma20, t - 1, False)

    price_up = c > c_prev + eps
    price_down = c < c_prev - eps
    above_ma20 = None if m is None else c > m + eps
    below_ma20 = None if m is None else c < m - eps
    above_ma10 = None if m10 is None else c > m10 + eps
    below_ma10 = None if m10 is None else c < m10 - eps
    break_ma20 = None if (m is None or m_prev is None) else (c_prev >= m_prev - eps and c < m - eps)
    dif_up = None if (d is None or d_prev is None) else d > d_prev + eps
    vol_up = None if rv is None else rv >= thresholds.rvol_up
    vol_down_price = None if rv is None else (rv >= thresholds.rvol_up and price_down)

    def st(v: Optional[bool]) -> str:
        return NO_DATA if v is None else (MET if v else NOT_MET)

    ev.conditions = [
        Condition("pos_shrink", st(pos_shrink(h_prev, h, eps)), h, h_prev),
        Condition("neg_shrink", st(neg_shrink(h_prev, h, eps)), h, h_prev),
        Condition("golden_cross", st(b2), h, eps),
        Condition("death_cross", st(s2), h, -eps),
        Condition("dif_up", st(dif_up), d, d_prev),
        Condition("above_ma10", st(above_ma10), c, m10),
        Condition("below_ma10", st(below_ma10), c, m10),
        Condition("above_ma20", st(above_ma20), c, m),
        Condition("below_ma20", st(below_ma20), c, m),
        Condition("trend_up", st(up_now), h, h_prev),
        Condition("trend_down", st(dn_now), h, h_prev),
        Condition("price_up", st(price_up), c, c_prev),
        Condition("break_ma20", st(break_ma20), c, m),
        Condition("volume_up", st(vol_up), rv, thresholds.rvol_up),
        Condition("volume_down_price", st(vol_down_price), rv, thresholds.rvol_up),
    ]

    codes: list[str] = []
    for hit, code in ((b1, NEG_WEAK), (b2, CROSS), (b3, TREND_UP), (s1, POS_WEAK), (s2, EXIT), (s3, TREND_DOWN)):
        if hit:
            codes.append(code)
    ev.codes = sort_by_priority(codes)
    return ev
