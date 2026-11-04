"""訊號分類：等級 × 方向（規格 2.2～2.5）。

classify_week() 只看陣列，不碰資料庫；同一次判斷使用同一個 eps。
首次減弱（NEG_WEAK／POS_WEAK）不因量價、MA20 未達門檻而延後（P-V06）。
2026-11-04 使用者定案：負柱首次縮短＝買進點、正柱首次縮短＝賣出點，每段負柱／正柱只發一次。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from .indicators import eps_at

# ---- 代碼、等級、方向 ----------------------------------------------------

NEG_WEAK = "NEG_WEAK"
CROSS = "CROSS"
POS_WEAK = "POS_WEAK"
RISK = "RISK"
ENTRY = "ENTRY"
EXIT = "EXIT"

LEVEL_OBSERVE = "observe"  # 觀察
LEVEL_ATTENTION = "attention"  # 注意
LEVEL_WARNING = "warning"  # 警示
LEVEL_CANDIDATE = "candidate"  # 候選

DIR_BULL = "bullish"  # ↗ 偏多
DIR_BEAR = "bearish"  # ↘ 偏空

SIGNAL_DEFS: dict[str, tuple[str, str]] = {
    NEG_WEAK: (LEVEL_CANDIDATE, DIR_BULL),  # 買進點
    CROSS: (LEVEL_OBSERVE, DIR_BULL),  # 2026-11-04 定案：金叉未達候選為「觀察」
    POS_WEAK: (LEVEL_CANDIDATE, DIR_BEAR),  # 賣出點
    RISK: (LEVEL_WARNING, DIR_BEAR),
    ENTRY: (LEVEL_CANDIDATE, DIR_BULL),
    EXIT: (LEVEL_CANDIDATE, DIR_BEAR),
}

# 主標籤優先序：賣出點 > 買進點 > EXIT > RISK > ENTRY > CROSS
PRIORITY = [POS_WEAK, NEG_WEAK, EXIT, RISK, ENTRY, CROSS]

# 首次減弱依 P-V06 不可關閉訂閱
MANDATORY_CODES = frozenset({NEG_WEAK, POS_WEAK})


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


def _first_in_segment(fn, hist: Sequence[Optional[float]], closes: Sequence[float], t: int,
                      positive: bool) -> bool:
    """同一段正柱（或負柱）裡的第一次縮短；之後再縮短不重發，直到柱體翻到另一邊。"""
    if not _first(fn, hist, closes, t):
        return False
    k = t - 1
    while k >= 2:
        h = hist[k - 1]
        if h is None or (h <= 0 if positive else h >= 0):
            break  # 往前到這段的起點
        if _first(fn, hist, closes, k):
            return False
        k -= 1
    return True


def classify_week(
    t: int,
    hist: Sequence[Optional[float]],
    closes: Sequence[float],
    ma20: Sequence[Optional[float]],
    rvol: Sequence[Optional[float]],
    thresholds: Thresholds = Thresholds(),
    warmup_index: int = 0,
    quality_ok: bool = True,
) -> WeekEvaluation:
    """判斷第 t 週（0 起算）成立哪些訊號。

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
    m_prev, m = ma20[t - 1], ma20[t]
    rv = rvol[t]

    pos_first = _first_in_segment(pos_shrink, hist, closes, t, positive=True)
    neg_first = _first_in_segment(neg_shrink, hist, closes, t, positive=False)
    pos_first_prev = t - 1 > warmup_index and _first(pos_shrink, hist, closes, t - 1)
    gold = golden_cross(h_prev, h, eps)
    death = death_cross(h_prev, h, eps)

    price_up = c > c_prev + eps
    price_down = c < c_prev - eps
    above_ma20 = None if m is None else c > m + eps
    break_ma20 = (
        None if (m is None or m_prev is None) else (c_prev >= m_prev - eps and c < m - eps)
    )
    vol_up = None if rv is None else rv >= thresholds.rvol_up
    vol_down_price = None if rv is None else (rv >= thresholds.rvol_up and price_down)
    weak_bg = pos_first or pos_first_prev

    def st(v: Optional[bool]) -> str:
        return NO_DATA if v is None else (MET if v else NOT_MET)

    ev.conditions = [
        Condition("pos_shrink", st(pos_shrink(h_prev, h, eps)), h, h_prev),
        Condition("neg_shrink", st(neg_shrink(h_prev, h, eps)), h, h_prev),
        Condition("golden_cross", st(gold), h, eps),
        Condition("death_cross", st(death), h, -eps),
        Condition("price_up", st(price_up), c, c_prev),
        Condition("above_ma20", st(above_ma20), c, m),
        Condition("break_ma20", st(break_ma20), c, m),
        Condition("volume_up", st(vol_up), rv, thresholds.rvol_up),
        Condition("volume_down_price", st(vol_down_price), rv, thresholds.rvol_up),
        Condition("weak_background", st(weak_bg)),
    ]

    codes: list[str] = []
    if neg_first:
        codes.append(NEG_WEAK)
    if pos_first:
        codes.append(POS_WEAK)
    if gold:
        # 量價資料不足時不升級（Q06）
        if above_ma20 and price_up and vol_up:
            codes.append(ENTRY)
        else:
            codes.append(CROSS)
    if weak_bg and (vol_down_price or break_ma20):
        codes.append(RISK)
    if death or (weak_bg and vol_down_price and break_ma20):
        codes.append(EXIT)
    ev.codes = sort_by_priority(codes)
    return ev
