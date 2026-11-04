"""Infoway 日股行情（https://docs.infoway.io）。

- 日 K：POST https://data.infoway.io/japan/v2/batch_kline，klineType=8，單檔每次最多 500 根，
  以 timestamp（含）往回翻頁；回傳由新到舊。
- 驗證：Header `apiKey`；金鑰只從環境變數 INFOWAY_API_KEY 讀取。
- 頻率：免費方案每秒 1 次，這裡每次請求間隔至少 1.1 秒。
- 文件未說明是否還原權值，因此不提供公司行動；遇到拆股時週線可能出現跳空。
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import holidays as _holidays

from app.engine.weekly import CorporateAction, DailyBar, Session, weekday_sessions
from app.providers.base import InstrumentInfo, ProviderError
from app.providers.jp_universe import JP_UNIVERSE

log = logging.getLogger(__name__)
KLINE_URL = "https://data.infoway.io/japan/v2/batch_kline"
DAY = 8
MAX_BARS = 500
MAX_PAGES = 3  # 最多約 1,500 個交易日（約 6 年），足夠 MACD 暖機（139 週）
MIN_INTERVAL = 1.1
TOKYO = ZoneInfo("Asia/Tokyo")


def jpx_holidays(start: date, end: date) -> set[date]:
    """東證休市日：日本國定假日（含補假）＋ 12/31、1/2、1/3。"""
    out = set(_holidays.Japan(years=range(start.year, end.year + 1)).keys())
    for y in range(start.year, end.year + 1):
        out |= {date(y, 12, 31), date(y, 1, 2), date(y, 1, 3)}
    return out


class InfowayJapanProvider:
    source = "infoway"

    def __init__(self, api_key: str, opener=None, sleep=time.sleep):
        self.api_key = api_key
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep
        self._last = 0.0

    # ---- 介面 ----
    def instruments(self, market: str) -> list[InstrumentInfo]:
        return [InstrumentInfo("JP", "XTKS", code, name, "JPY", 100) for code, name in JP_UNIVERSE]

    def make_instrument(self, symbol: str, name: str | None = None) -> InstrumentInfo:
        """清單外的東證代號（使用者匯入）；代號錯誤時抓日 K 會回 508，該檔標記為資料異常。"""
        return InstrumentInfo("JP", "XTKS", symbol, (name or symbol)[:80], "JPY", 100)

    def sessions(self, market: str, start: date, end: date) -> list[Session]:
        return weekday_sessions(start, end, jpx_holidays(start, end))

    def corporate_actions(self, market: str, symbol: str) -> list[CorporateAction]:
        return []

    def final_through(self, market: str, as_of: date) -> date:
        # 東京 15:30 收盤；排程在收盤後執行，當日 K 已定稿
        now = datetime.now(TOKYO)
        if as_of >= now.date() and (now.hour, now.minute) < (15, 30):
            return as_of - timedelta(days=1)
        return as_of

    def daily_bars(self, market: str, symbol: str, start: date, end: date) -> list[DailyBar]:
        code = f"{symbol}.JP"
        end_ts = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), TOKYO).timestamp()) - 1
        # 只需要幾天時少抓一些，降低回應大小
        need = min(MAX_BARS, max(5, (end - start).days + 5))
        bars: dict[date, DailyBar] = {}
        ts = end_ts
        for _ in range(MAX_PAGES):
            rows = self._kline(code, need, ts)
            if not rows:
                break
            oldest = None
            for r in rows:
                t = int(r["t"])
                d = datetime.fromtimestamp(t, TOKYO).date()
                oldest = t if oldest is None else min(oldest, t)
                if start <= d <= end:
                    bars[d] = DailyBar(d, float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), float(r["v"]))
            if oldest is None or datetime.fromtimestamp(oldest, TOKYO).date() <= start or len(rows) < need:
                break
            ts = oldest - 1
        return [bars[d] for d in sorted(bars)]

    # ---- HTTP ----
    def _kline(self, code: str, num: int, ts: int) -> list[dict]:
        wait = self._last + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        body = json.dumps({"klineType": DAY, "klineNum": num, "codes": code, "timestamp": ts}).encode()
        req = urllib.request.Request(KLINE_URL, data=body, method="POST",
                                     headers={"apiKey": self.api_key, "Content-Type": "application/json"})
        try:
            with self._open(req, timeout=20) as r:
                payload = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Infoway HTTP {e.code}") from None
        finally:
            self._last = time.monotonic()
        ret = payload.get("ret")
        if ret != 200:
            msg = f"Infoway ret={ret} {payload.get('msg', '')}".strip()
            if ret in (508, 514):  # 查無代號、無權限：只影響這一檔
                raise ProviderError(msg)
            raise RuntimeError(msg)
        for item in payload.get("data") or []:
            if item.get("s") == code:
                return item.get("respList") or []
        return []
