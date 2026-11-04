"""行情來源轉接層。實際供應商待確認（規格 X-02、X-03），一律實作此介面以隔離。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from app.engine.weekly import CorporateAction, DailyBar, Session


class ProviderError(Exception):
    """單一標的抓取失敗（無權限、查無代號等），不應讓整個市場作業失敗。"""


@dataclass(frozen=True)
class InstrumentInfo:
    market: str
    mic: str
    symbol: str
    name: str
    currency: str
    lot_size: int = 1


class MarketDataProvider(Protocol):
    source: str

    def instruments(self, market: str) -> list[InstrumentInfo]: ...

    def sessions(self, market: str, start: date, end: date) -> list[Session]: ...

    def daily_bars(self, market: str, symbol: str, start: date, end: date) -> list[DailyBar]: ...

    def corporate_actions(self, market: str, symbol: str) -> list[CorporateAction]: ...

    def final_through(self, market: str, as_of: date) -> date:
        """供應商已標記為最終的最後日期。"""
        ...
