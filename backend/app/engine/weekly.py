"""公司行動調整、週線聚合與量比 RVOL（規格 3.2、3.3）。"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Iterable, Optional, Sequence


@dataclass(frozen=True)
class DailyBar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float  # 統一以「股」為單位


@dataclass(frozen=True)
class CorporateAction:
    effective_date: date
    price_factor: float  # 2:1 拆股為 0.5
    kind: str = "split"

    @property
    def volume_factor(self) -> float:
        return 1.0 / self.price_factor


@dataclass(frozen=True)
class Session:
    """交易日曆的一個交易時段；weight 為時段等值（提早收盤以交易分鐘比例折算）。"""

    local_date: date
    weight: float = 1.0


class QualityError(ValueError):
    """資料品質不合格：需隔離，不得進入計算。"""


def validate_bar(bar: DailyBar) -> None:
    if min(bar.open, bar.high, bar.low, bar.close) <= 0:
        raise QualityError(f"{bar.date} 價格必須大於 0")
    if not (bar.low <= bar.open <= bar.high and bar.low <= bar.close <= bar.high):
        raise QualityError(f"{bar.date} 高低價倒置")
    if bar.volume < 0:
        raise QualityError(f"{bar.date} 成交量為負")


def adjust_for_actions(bars: Sequence[DailyBar], actions: Iterable[CorporateAction]) -> list[DailyBar]:
    """向後調整：生效日之前的價格 × price_factor、量 × volume_factor。

    原始資料不被覆寫，回傳新的調整後序列；現金股利預設不復權（不在此處理）。
    """
    acts = sorted(actions, key=lambda a: a.effective_date)
    out: list[DailyBar] = []
    for b in bars:
        pf = 1.0
        for a in acts:
            if b.date < a.effective_date:
                pf *= a.price_factor
        if pf == 1.0:
            out.append(b)
        else:
            out.append(
                replace(
                    b,
                    open=b.open * pf,
                    high=b.high * pf,
                    low=b.low * pf,
                    close=b.close * pf,
                    volume=b.volume / pf,
                )
            )
    return out


def week_key(d: date) -> date:
    """week_key = 當地週一日期（市場另存於上層）。"""
    return d - timedelta(days=d.weekday())


WEEK_PROVISIONAL = "provisional"  # 本週暫定
WEEK_PENDING = "pending"  # 待確認（週已結束但資料未齊）
WEEK_CONFIRMED = "confirmed"  # 已確認


@dataclass
class WeeklyBar:
    week_key: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    elapsed_sessions: float  # 已完成時段等值
    expected_sessions: float  # 本週應有時段等值
    elapsed_count: int
    expected_count: int
    status: str
    session_dates: list[date] = field(default_factory=list)


def aggregate_weekly(
    bars: Sequence[DailyBar],
    sessions: Sequence[Session],
    as_of: date,
    final_through: Optional[date] = None,
) -> list[WeeklyBar]:
    """依交易日曆把日線聚合為週線。

    - 無交易的週不產生 K 棒；缺漏日不補零。
    - as_of：目前資料截止的當地日期。
    - final_through：供應商已標記最終的最後日期（預設等於 as_of）。
    """
    final_through = final_through or as_of
    for b in bars:
        validate_bar(b)
    by_week_sessions: dict[date, list[Session]] = {}
    for s in sessions:
        by_week_sessions.setdefault(week_key(s.local_date), []).append(s)
    weight_of = {s.local_date: s.weight for s in sessions}

    by_week: dict[date, list[DailyBar]] = {}
    for b in sorted(bars, key=lambda x: x.date):
        if b.date not in weight_of:
            # 非交易日資料視為來源異常，不納入
            continue
        by_week.setdefault(week_key(b.date), []).append(b)

    out: list[WeeklyBar] = []
    for wk in sorted(by_week):
        days = by_week[wk]
        exp = sorted(by_week_sessions.get(wk, []), key=lambda s: s.local_date)
        last_session = exp[-1].local_date if exp else days[-1].date
        have = {d.date for d in days}
        complete = all(s.local_date in have for s in exp)
        week_over = last_session <= as_of
        if week_over and complete and last_session <= final_through:
            status = WEEK_CONFIRMED
        elif week_over:
            status = WEEK_PENDING
        else:
            status = WEEK_PROVISIONAL
        out.append(
            WeeklyBar(
                week_key=wk,
                open=days[0].open,
                high=max(d.high for d in days),
                low=min(d.low for d in days),
                close=days[-1].close,
                volume=sum(d.volume for d in days),
                elapsed_sessions=sum(weight_of[d.date] for d in days),
                expected_sessions=sum(s.weight for s in exp),
                elapsed_count=len(days),
                expected_count=len(exp),
                status=status,
                session_dates=[d.date for d in days],
            )
        )
    return out


RVOL_LOOKBACK = 20


def rvol_series(weeks: Sequence[WeeklyBar], lookback: int = RVOL_LOOKBACK) -> list[Optional[float]]:
    """RVOL =（本週累計量 ÷ 已完成時段等值）÷（前 20 個有效完成週總量 ÷ 時段等值總和）。

    只用「已確認」的週作為基準；不足 20 週回傳 None（介面顯示「資料不足」）。
    """
    out: list[Optional[float]] = []
    for i, w in enumerate(weeks):
        base = [x for x in weeks[:i] if x.status == WEEK_CONFIRMED][-lookback:]
        if len(base) < lookback or w.elapsed_sessions <= 0:
            out.append(None)
            continue
        base_vol = sum(x.volume for x in base)
        base_eq = sum(x.elapsed_sessions for x in base)
        if base_vol <= 0 or base_eq <= 0:
            out.append(None)
            continue
        out.append((w.volume / w.elapsed_sessions) / (base_vol / base_eq))
    return out


def weekday_sessions(start: date, end: date, holidays: Iterable[date] = (), early_closes: dict[date, float] | None = None) -> list[Session]:
    """以「週一至週五扣除休市日」建立簡易交易日曆；early_closes 給交易分鐘比例。"""
    hol = set(holidays)
    early = early_closes or {}
    out: list[Session] = []
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in hol:
            out.append(Session(d, early.get(d, 1.0)))
        d += timedelta(days=1)
    return out
