"""FinMind 台股行情（https://finmind.github.io ，API v4）。

- GET https://api.finmindtrade.com/api/v4/data?dataset=...&data_id=...&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
  回傳 {"msg", "status", "data": [...]}；Header `Authorization: Bearer <token>`（金鑰選填，環境變數 FINMIND_TOKEN）。
- 額度：有金鑰每小時 600 次、沒有 300 次；超過回 402。
- 日 K：TaiwanStockPrice（date, stock_id, open, max, min, close, Trading_Volume），未還原。
- 除權息：TaiwanStockDividendResult 的 reference_price / before_price 轉成公司行動，週線因此還原權值。
- 股票清單：TaiwanStockInfo（上市 twse、上櫃 tpex）。
- 交易日：TaiwanStockTradingDate；抓不到時改用國定假日推算。
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import holidays as _holidays

from app.engine.weekly import CorporateAction, DailyBar, Session, weekday_sessions
from app.providers.base import InstrumentInfo, ProviderError, QuotaExhausted

log = logging.getLogger(__name__)
DATA_URL = "https://api.finmindtrade.com/api/v4/data"
TAIPEI = ZoneInfo("Asia/Taipei")
MIN_INTERVAL = 0.2
_CODE_RE = re.compile(r"^[0-9]{4}$|^00[0-9]{2,4}[A-Z]?$")  # 一般股票 4 碼、ETF 00 開頭
MIC = {"twse": "XTAI", "tpex": "ROCO"}


def tw_holidays(start: date, end: date) -> set[date]:
    return set(_holidays.Taiwan(years=range(start.year, end.year + 1)).keys())


class FinMindTaiwanProvider:
    source = "finmind"
    sync_on_startup = False  # 清單約 2,000 檔，改在盤後作業時同步，避免每次冷啟動都打 API

    def __init__(self, token: str = "", opener=None, sleep=time.sleep):
        self.token = token
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep
        self._last = 0.0
        self._range: dict[str, tuple[date, date]] = {}

    # ---- 介面 ----
    def instruments(self, market: str) -> list[InstrumentInfo]:
        out: dict[str, InstrumentInfo] = {}
        for r in self._get({"dataset": "TaiwanStockInfo"}):
            sym, kind = str(r.get("stock_id", "")).strip(), r.get("type")
            if kind not in MIC or not _CODE_RE.match(sym) or sym in out:
                continue
            out[sym] = InstrumentInfo("TW", MIC[kind], sym, str(r.get("stock_name") or sym)[:80], "TWD", 1000)
        return list(out.values())

    def make_instrument(self, symbol: str, name: str | None = None) -> InstrumentInfo:
        return InstrumentInfo("TW", "XTAI", symbol, (name or symbol)[:80], "TWD", 1000)

    def sessions(self, market: str, start: date, end: date) -> list[Session]:
        hol = tw_holidays(start, end)
        try:
            days = {date.fromisoformat(r["date"]) for r in self._get({"dataset": "TaiwanStockTradingDate",
                                                                      "start_date": start.isoformat(),
                                                                      "end_date": end.isoformat()})}
        except Exception as e:  # 交易日表抓不到：用國定假日推算
            log.warning("FinMind 交易日表無法取得，改用假日推算：%s", e)
            days = set()
        if not days:
            return weekday_sessions(start, end, hol)
        last = max(days)
        known = [Session(d) for d in sorted(days) if start <= d <= end]
        # 交易日表只到今天；之後的日子用平日扣掉國定假日預估
        return known + weekday_sessions(last + timedelta(days=1), end, hol) if last < end else known

    def corporate_actions(self, market: str, symbol: str) -> list[CorporateAction]:
        rng = self._range.pop(symbol, None)
        if rng is None:
            return []
        out = []
        for r in self._get({"dataset": "TaiwanStockDividendResult", "data_id": symbol,
                            "start_date": rng[0].isoformat(), "end_date": rng[1].isoformat()}):
            try:
                before, ref = float(r["before_price"]), float(r["reference_price"])
            except (KeyError, TypeError, ValueError):
                continue
            if before > 0 and 0 < ref < before:
                out.append(CorporateAction(date.fromisoformat(r["date"]), ref / before, "dividend"))
        return out

    def final_through(self, market: str, as_of: date) -> date:
        # 13:30 收盤；FinMind 傍晚更新當日資料
        now = datetime.now(TAIPEI)
        if as_of >= now.date() and now.hour < 18:
            return as_of - timedelta(days=1)
        return as_of

    def daily_bars(self, market: str, symbol: str, start: date, end: date) -> list[DailyBar]:
        rows = self._get({"dataset": "TaiwanStockPrice", "data_id": symbol,
                          "start_date": start.isoformat(), "end_date": end.isoformat()})
        bars: dict[date, DailyBar] = {}
        for r in rows:
            o, h, l, c = (float(r.get(k) or 0) for k in ("open", "max", "min", "close"))
            if min(o, h, l, c) <= 0:  # 當日無成交
                continue
            d = date.fromisoformat(r["date"])
            bars[d] = DailyBar(d, o, h, l, c, float(r.get("Trading_Volume") or 0))
        if not bars and (end - start).days > 30:
            raise ProviderError(f"FinMind 查無 {symbol} 的日線（代號錯誤或已下市）")
        if bars:
            self._range[symbol] = (min(bars), max(bars))
        return [bars[d] for d in sorted(bars)]

    # ---- HTTP ----
    def _get(self, params: dict) -> list[dict]:
        wait = self._last + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        req = urllib.request.Request(f"{DATA_URL}?{urllib.parse.urlencode(params)}", headers=headers)
        try:
            with self._open(req, timeout=30) as r:
                payload = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 402:
                raise QuotaExhausted("FinMind 本小時請求額度已用完") from None
            if e.code == 400 and params.get("data_id"):
                raise ProviderError(f"FinMind HTTP 400（{params['data_id']}）") from None
            raise RuntimeError(f"FinMind HTTP {e.code}") from None
        finally:
            self._last = time.monotonic()
        if payload.get("status") not in (200, None):
            msg = f"FinMind {payload.get('status')} {payload.get('msg', '')}".strip()
            if payload.get("status") == 402:
                raise QuotaExhausted("FinMind 本小時請求額度已用完")
            raise ProviderError(msg) if params.get("data_id") else RuntimeError(msg)
        return payload.get("data") or []
