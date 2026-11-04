"""FinMind 台股行情（https://finmind.github.io ，API v4）。

- GET https://api.finmindtrade.com/api/v4/data?dataset=...&data_id=...&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
  回傳 {"msg", "status", "data": [...]}；Header `Authorization: Bearer <token>`（金鑰選填，環境變數 FINMIND_TOKEN）。
- 額度：有金鑰每小時 600 次、沒有 300 次；超過回 402。
- 日 K：TaiwanStockPrice（date, stock_id, open, max, min, close, Trading_Volume），未還原。
- 除權息：TaiwanStockDividendResult 的 reference_price / before_price 轉成公司行動，週線因此還原權值。
- 股票分割（含反分割）TaiwanStockSplitPrice、面額變更 TaiwanStockParValueChange：整個市場一次查 10 年；
  減資 TaiwanStockCapitalReductionReferencePrice：逐檔查。都轉成公司行動，避免分割當週出現價格斷崖。
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


_UNLOADED = object()


def _ratio(before, after) -> float | None:
    """生效日前的價格要乘上的係數 = 生效後參考價 ÷ 生效前收盤。"""
    try:
        b, a = float(before), float(after)
    except (TypeError, ValueError):
        return None
    return a / b if b > 0 and a > 0 else None


def _history_start() -> str:
    return (date.today() - timedelta(days=365 * 10 + 30)).isoformat()


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
        self._market_events = _UNLOADED

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
        """除權息、減資（依本次抓到的日線區間）＋ 股票分割、面額變更（全期，整個市場一次查）。"""
        out = list(self._events(symbol))
        if self._market_events is None:  # 整個市場查不到時，改逐檔查全期
            q = {"data_id": symbol, "start_date": _history_start()}
            out += self._split_rows(self._get_opt({"dataset": "TaiwanStockSplitPrice", **q}))
            out += self._par_rows(self._get_opt({"dataset": "TaiwanStockParValueChange", **q}))
        rng = self._range.pop(symbol, None)
        if rng is None:
            return out
        s, e = rng[0].isoformat(), rng[1].isoformat()
        for r in self._get({"dataset": "TaiwanStockDividendResult", "data_id": symbol, "start_date": s, "end_date": e}):
            f = _ratio(r.get("before_price"), r.get("reference_price"))
            if f is not None and f < 1:
                out.append(CorporateAction(date.fromisoformat(r["date"]), f, "dividend"))
        for r in self._get_opt({"dataset": "TaiwanStockCapitalReductionReferencePrice", "data_id": symbol,
                            "start_date": s, "end_date": e}):
            f = _ratio(r.get("ClosingPriceonTheLastTradingDay"), r.get("PostReductionReferencePrice"))
            if f is not None and f != 1:
                out.append(CorporateAction(date.fromisoformat(r["date"]), f, "capital_reduction"))
        return out

    def _events(self, symbol: str) -> list[CorporateAction]:
        if self._market_events is _UNLOADED:
            start = _history_start()
            try:
                rows = {}
                for a_sym, a in ([(str(r.get("stock_id")), a) for r in self._get({"dataset": "TaiwanStockSplitPrice", "start_date": start})
                                  for a in self._split_rows([r])]
                                 + [(str(r.get("stock_id")), a) for r in self._get({"dataset": "TaiwanStockParValueChange", "start_date": start})
                                    for a in self._par_rows([r])]):
                    rows.setdefault(a_sym, []).append(a)
                if not rows:  # 十年內全市場不可能沒有分割或面額變更：視為查詢未開放
                    raise RuntimeError("整批查詢沒有資料")
                self._market_events = rows
            except QuotaExhausted:
                raise
            except Exception as e:
                log.warning("FinMind 分割／面額變更清單無法整批取得，改逐檔查詢：%s", e)
                self._market_events = None
        return (self._market_events or {}).get(symbol, [])

    @staticmethod
    def _split_rows(rows: list[dict]) -> list[CorporateAction]:
        out = []
        for r in rows:
            f = _ratio(r.get("before_price"), r.get("after_price"))
            if f is not None and f != 1:
                out.append(CorporateAction(date.fromisoformat(r["date"]), f, "split"))
        return out

    @staticmethod
    def _par_rows(rows: list[dict]) -> list[CorporateAction]:
        out = []
        for r in rows:
            f = _ratio(r.get("before_close"), r.get("after_ref_close"))
            if f is not None and f != 1:
                out.append(CorporateAction(date.fromisoformat(r["date"]), f, "par_value"))
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
    def _get_opt(self, params: dict) -> list[dict]:
        """輔助資料（分割、減資）：查不到只記錄，不讓整檔股票變成無法取得。額度用完仍往上拋。"""
        try:
            return self._get(params)
        except QuotaExhausted:
            raise
        except Exception as e:
            log.warning("FinMind %s 查詢失敗：%s", params.get("dataset"), e)
            return []

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
