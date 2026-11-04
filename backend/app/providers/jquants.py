"""J-Quants 日股行情（https://jpx-jquants.com/en/spec ，API V2）。

- 日 K：GET https://api.jquants.com/v2/equities/bars/daily?code=72030&from=YYYYMMDD&to=YYYYMMDD
  回傳 {"data": [{Date, Code, O, H, L, C, Vo, AdjFactor, ...}], "pagination_key"?}。
- 驗證：Header `x-api-key`；金鑰只從環境變數 JQUANTS_API_KEY 讀取。
- 存未還原價，AdjFactor ≠ 1 的日子轉成公司行動（除權息日前的價格 × AdjFactor），
  這樣日後拆股不需要重抓整段歷史。
- 方案（JQUANTS_PLAN）：free 每分鐘 5 次、2 年歷史且延遲 12 週；light 每分鐘 60 次、5 年；
  standard 120 次、10 年；premium 500 次、20 年。
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import holidays as _holidays

from app.engine.weekly import CorporateAction, DailyBar, Session, weekday_sessions
from app.providers.base import InstrumentInfo, ProviderError
from app.providers.jp_universe import JP_UNIVERSE

log = logging.getLogger(__name__)
BARS_URL = "https://api.jquants.com/v2/equities/bars/daily"
TOKYO = ZoneInfo("Asia/Tokyo")
# 方案：每分鐘請求數、可取得的歷史年數
PLANS = {"free": (5, 2), "light": (60, 5), "standard": (120, 10), "premium": (500, 20)}
MAX_PAGES = 20


def jpx_holidays(start: date, end: date) -> set[date]:
    """東證休市日：日本國定假日（含補假）＋ 12/31、1/2、1/3。"""
    out = set(_holidays.Japan(years=range(start.year, end.year + 1)).keys())
    for y in range(start.year, end.year + 1):
        out |= {date(y, 12, 31), date(y, 1, 2), date(y, 1, 3)}
    return out


def api_code(symbol: str) -> str:
    """東證 4 碼代號 → J-Quants 5 碼（末碼 0）。"""
    return symbol + "0" if len(symbol) == 4 else symbol


class JQuantsJapanProvider:
    source = "jquants"

    def __init__(self, api_key: str, plan: str = "light", opener=None, sleep=time.sleep):
        self.api_key = api_key
        rpm, years = PLANS.get(plan, PLANS["light"])
        self.min_interval = 60.0 / rpm + 0.05
        self.history_days = int(365 * years) - 7
        self.delay_days = 84 if plan == "free" else 0  # 免費方案延遲 12 週
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep
        self._last = 0.0
        self._actions: dict[str, list[CorporateAction]] = {}

    # ---- 介面 ----
    def instruments(self, market: str) -> list[InstrumentInfo]:
        return [InstrumentInfo("JP", "XTKS", code, name, "JPY", 100) for code, name in JP_UNIVERSE]

    def make_instrument(self, symbol: str, name: str | None = None) -> InstrumentInfo:
        """清單外的東證代號（使用者匯入）；代號錯誤時抓不到日 K，該檔標記為無法取得。"""
        return InstrumentInfo("JP", "XTKS", symbol, (name or symbol)[:80], "JPY", 100)

    def sessions(self, market: str, start: date, end: date) -> list[Session]:
        return weekday_sessions(start, end, jpx_holidays(start, end))

    def corporate_actions(self, market: str, symbol: str) -> list[CorporateAction]:
        # 由 daily_bars 一併取得（同一次請求的 AdjFactor），不另外打 API
        return self._actions.get(symbol, [])

    def final_through(self, market: str, as_of: date) -> date:
        # 東京 15:30 收盤；J-Quants 約 16:30 更新當日資料
        now = datetime.now(TOKYO)
        if as_of >= now.date() and (now.hour, now.minute) < (16, 30):
            return as_of - timedelta(days=1)
        return as_of

    def daily_bars(self, market: str, symbol: str, start: date, end: date) -> list[DailyBar]:
        end = min(end, date.today() - timedelta(days=self.delay_days))
        start = max(start, end - timedelta(days=self.history_days))  # 超出方案範圍會被拒絕
        if start > end:
            return []
        params = {"code": api_code(symbol), "from": start.strftime("%Y%m%d"), "to": end.strftime("%Y%m%d")}
        bars: dict[date, DailyBar] = {}
        actions: list[CorporateAction] = []
        for _ in range(MAX_PAGES):
            payload = self._get(params)
            for r in payload.get("data") or []:
                if r.get("C") is None:  # 停牌或無成交
                    continue
                d = date.fromisoformat(str(r["Date"])[:10])
                bars[d] = DailyBar(d, float(r["O"]), float(r["H"]), float(r["L"]), float(r["C"]), float(r.get("Vo") or 0))
                f = r.get("AdjFactor")
                if f not in (None, "", 1, 1.0) and float(f) > 0 and float(f) != 1.0:
                    actions.append(CorporateAction(d, float(f)))
            key = payload.get("pagination_key")
            if not key:
                break
            params = {**params, "pagination_key": key}
        if not bars and (end - start).days > 30:
            raise ProviderError(f"J-Quants 查無 {symbol} 的日線（代號錯誤或已下市）")
        self._actions[symbol] = actions
        return [bars[d] for d in sorted(bars)]

    # ---- HTTP ----
    def _get(self, params: dict, retry: bool = True) -> dict:
        wait = self._last + self.min_interval - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        req = urllib.request.Request(f"{BARS_URL}?{urllib.parse.urlencode(params)}", headers={"x-api-key": self.api_key})
        try:
            with self._open(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            msg = ""
            try:
                msg = json.loads(e.read()).get("message", "")
            except Exception:
                pass
            if e.code == 429 and retry:  # 超過每分鐘上限：等一分鐘再試一次
                self._last = time.monotonic()
                self._sleep(60)
                return self._get(params, retry=False)
            if e.code == 400:  # 參數或方案範圍問題：只影響這一檔
                raise ProviderError(f"J-Quants HTTP 400 {msg}".strip()) from None
            raise RuntimeError(f"J-Quants HTTP {e.code} {msg}".strip()) from None
        finally:
            self._last = time.monotonic()
