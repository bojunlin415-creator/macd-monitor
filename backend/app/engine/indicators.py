"""MACD、週均線、容差與暖機（規格 2.1）。

純計算、無 I/O，可離線重現。所有數值以雙精度計算，只在顯示時四捨五入。
序列中的 None 代表「尚未定義」（例如暖機前的 EMA），介面需顯示「資料不足」而非 0。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

ENGINE_VERSION = "1.0.0"

Num = Optional[float]


@dataclass(frozen=True)
class MacdParams:
    fast: int = 12
    slow: int = 26
    signal: int = 9

    def validate(self) -> None:
        f, s, g = self.fast, self.slow, self.signal
        if not all(isinstance(x, int) for x in (f, s, g)):
            raise ValueError("MACD 參數必須是整數。")
        if not (2 <= f < s <= 104):
            raise ValueError("快線週數需介於 2 以上，且慢線週數必須大於快線週數（最多 104）。")
        if not (2 <= g <= 52):
            raise ValueError("訊號線週數需介於 2～52。")

    @property
    def warmup_weeks(self) -> int:
        """W = max(60, 5s + g)；預設 12/26/9 為 139 週。"""
        return max(60, 5 * self.slow + self.signal)


def ema(values: Sequence[Num], n: int) -> list[Num]:
    """以第 n 筆前 n 筆 SMA 初始化，之後 EMA = α·x + (1−α)·EMA[t−1]。

    輸入前段的 None 會被略過（例如 DIF 尚未定義的區段）；
    一旦開始有效值後再出現 None，視為序列中斷，後續皆為 None。
    """
    out: list[Num] = [None] * len(values)
    alpha = 2.0 / (n + 1)
    start = next((i for i, v in enumerate(values) if v is not None), None)
    if start is None:
        return out
    seed_end = start + n - 1
    if seed_end >= len(values):
        return out
    window = values[start : seed_end + 1]
    if any(v is None for v in window):
        return out
    prev = sum(window) / n  # type: ignore[arg-type]
    out[seed_end] = prev
    for i in range(seed_end + 1, len(values)):
        v = values[i]
        if v is None:
            break
        prev = alpha * v + (1 - alpha) * prev
        out[i] = prev
    return out


@dataclass
class MacdSeries:
    dif: list[Num]
    dea: list[Num]
    hist: list[Num]


def macd(closes: Sequence[float], params: MacdParams = MacdParams()) -> MacdSeries:
    params.validate()
    ef = ema(closes, params.fast)
    es = ema(closes, params.slow)
    dif: list[Num] = [
        (a - b) if (a is not None and b is not None) else None for a, b in zip(ef, es)
    ]
    dea = ema(dif, params.signal)  # 以最初 g 個有效 DIF 的平均初始化
    hist: list[Num] = [
        (a - b) if (a is not None and b is not None) else None for a, b in zip(dif, dea)
    ]  # 柱體倍率固定為 1
    return MacdSeries(dif=dif, dea=dea, hist=hist)


def sma(values: Sequence[float], n: int) -> list[Num]:
    """最近 n 個有效連續週收盤平均；不足 n 週為 None（Q03）。"""
    out: list[Num] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


MA_PERIODS = (5, 10, 20, 60)


def moving_averages(closes: Sequence[float]) -> dict[int, list[Num]]:
    return {n: sma(closes, n) for n in MA_PERIODS}


def eps_at(prev_close: Optional[float]) -> float:
    """eps[t] = max(1e-8, 1e-6 × |C[t−1]|)，原幣價格單位。"""
    if prev_close is None:
        return 1e-8
    return max(1e-8, 1e-6 * abs(prev_close))


def warmup_ok(index: int, params: MacdParams = MacdParams()) -> bool:
    """index 為 0 起算的週序；需至少 W 個有效連續週。"""
    return index + 1 >= params.warmup_weeks
