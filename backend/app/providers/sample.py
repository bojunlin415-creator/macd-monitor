"""示意資料來源：以固定亂數種子產生可重現的日 K，供開發、展示與測試。

代號與名稱皆為虛構（T／U／J 開頭），不是真實行情；正式上線前須換成已授權供應商（X-02、X-03）。
"""
from __future__ import annotations

import hashlib
import math
import random
from datetime import date, timedelta

from app.engine.weekly import CorporateAction, DailyBar, Session, weekday_sessions
from app.providers.base import InstrumentInfo

MARKETS = {
    "TW": ("XTAI", "TWD", 1000),
    "US": ("XNAS", "USD", 1),
    "JP": ("XTKS", "JPY", 100),
}

NAMES = {
    "TW": ["示意半導體", "示意電子", "示意金控", "示意航運", "示意鋼鐵", "示意生技", "示意光電", "示意電信"],
    "US": ["示意科技", "示意軟體", "示意零售", "示意能源", "示意醫療", "示意銀行", "示意晶片", "示意消費"],
    "JP": ["示意汽車", "示意商社", "示意電機", "示意精工", "示意遊戲", "示意製藥", "示意材料", "示意通訊"],
}

HISTORY_START = date(2016, 1, 4)


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:12], 16)


class SampleProvider:
    source = "sample"

    def instruments(self, market: str) -> list[InstrumentInfo]:
        mic, ccy, lot = MARKETS[market]
        return [
            InstrumentInfo(market, mic, f"{market[0]}{i + 1:03d}", name, ccy, lot)
            for i, name in enumerate(NAMES[market])
        ]

    def sessions(self, market: str, start: date, end: date) -> list[Session]:
        return weekday_sessions(start, end)

    def corporate_actions(self, market: str, symbol: str) -> list[CorporateAction]:
        # 每市場第 2 檔在 2024-07-01 做 2:1 拆股，用來驗證復權
        if symbol.endswith("002"):
            return [CorporateAction(date(2024, 7, 1), 0.5)]
        return []

    def final_through(self, market: str, as_of: date) -> date:
        return as_of

    def daily_bars(self, market: str, symbol: str, start: date, end: date) -> list[DailyBar]:
        rnd = random.Random(_seed(market, symbol))
        price = 20 + rnd.random() * 180
        base_vol = 1e5 * (1 + rnd.random() * 20)
        cycle = 20 + rnd.random() * 40  # 週期（交易日）
        drift = (rnd.random() - 0.45) * 0.0006
        split_dates = {a.effective_date: a for a in self.corporate_actions(market, symbol)}
        out: list[DailyBar] = []
        for i, s in enumerate(weekday_sessions(HISTORY_START, end)):
            d = s.local_date
            if d in split_dates:
                price *= split_dates[d].price_factor
                base_vol /= split_dates[d].price_factor
            wave = math.sin(i / cycle * 2 * math.pi) * 0.004
            ret = drift + wave + rnd.gauss(0, 0.015)
            o = price
            c = max(0.5, price * (1 + ret))
            hi = max(o, c) * (1 + abs(rnd.gauss(0, 0.006)))
            lo = min(o, c) * (1 - abs(rnd.gauss(0, 0.006)))
            vol = base_vol * (1 + abs(ret) * 25) * (0.6 + rnd.random() * 0.8)
            price = c
            if d >= start:
                out.append(DailyBar(d, round(o, 4), round(hi, 4), round(lo, 4), round(c, 4), round(vol)))
        return out
