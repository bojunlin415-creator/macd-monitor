import io
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.providers.base import ProviderError
from app.providers.infoway import InfowayJapanProvider, jpx_holidays

TOKYO = ZoneInfo("Asia/Tokyo")


def _ts(d):
    return str(int(datetime(d.year, d.month, d.day, 9, tzinfo=TOKYO).timestamp()))


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_opener(pages, calls):
    def opener(req, timeout=0):
        body = json.loads(req.data)
        calls.append((dict(req.header_items()), body))
        return FakeResp(json.dumps(pages[len(calls) - 1]).encode())
    return opener


def bar(d, c):
    return {"t": _ts(d), "o": str(c), "h": str(c + 1), "l": str(c - 1), "c": str(c), "v": "1000", "vw": "0", "pc": "0%", "pca": "0"}


def test_daily_bars_parses_and_sends_key():
    calls = []
    page = {"ret": 200, "msg": "success", "data": [{"s": "7203.JP", "respList": [bar(date(2026, 11, 4), 3000), bar(date(2026, 11, 3), 2990)]}]}
    p = InfowayJapanProvider("k", opener=make_opener([page], calls), sleep=lambda s: None)
    out = p.daily_bars("JP", "7203", date(2026, 11, 3), date(2026, 11, 4))
    assert [b.date for b in out] == [date(2026, 11, 3), date(2026, 11, 4)]
    assert out[-1].close == 3000.0
    headers, body = calls[0]
    assert headers.get("Apikey") == "k" and body["klineType"] == 8 and body["codes"] == "7203.JP"


def test_daily_bars_pages_backwards():
    calls = []
    first = {"ret": 200, "data": [{"s": "7203.JP", "respList": [bar(date(2026, 11, 4), 1)] * 1}]}
    p = InfowayJapanProvider("k", opener=make_opener([first, {"ret": 200, "data": []}], calls), sleep=lambda s: None)
    import app.providers.infoway as iw
    old = iw.MAX_BARS
    iw.MAX_BARS = 1
    try:
        p.daily_bars("JP", "7203", date(2026, 1, 1), date(2026, 11, 4))
    finally:
        iw.MAX_BARS = old
    assert len(calls) == 2 and calls[1][1]["timestamp"] == int(_ts(date(2026, 11, 4))) - 1


def test_no_permission_is_per_symbol_error():
    p = InfowayJapanProvider("k", opener=make_opener([{"ret": 514, "msg": "no permission"}], []), sleep=lambda s: None)
    with pytest.raises(ProviderError):
        p.daily_bars("JP", "7203", date(2026, 11, 3), date(2026, 11, 4))


def test_jpx_calendar_skips_holidays():
    hol = jpx_holidays(date(2026, 1, 1), date(2026, 12, 31))
    assert date(2026, 1, 2) in hol and date(2026, 12, 31) in hol and date(2026, 11, 3) in hol  # 文化の日
    sess = InfowayJapanProvider("k").sessions("JP", date(2026, 11, 2), date(2026, 11, 6))
    assert [s.local_date for s in sess] == [date(2026, 11, 2), date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)]


def test_switching_source_delists_sample_jp(monkeypatch):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.db import models as M
    from app.config import settings
    from app.seed import seed
    eng = create_engine("sqlite://")
    M.Base.metadata.create_all(eng)
    with sessionmaker(bind=eng)() as db:
        seed(db)
        db.flush()
        object.__setattr__(settings, "infoway_api_key", "k")
        try:
            seed(db)
            db.flush()
        finally:
            object.__setattr__(settings, "infoway_api_key", "")
        jp = db.scalars(select(M.Instrument).where(M.Instrument.market_id == "JP", M.Instrument.delisted_on.is_(None))).all()
        assert {i.license_status for i in jp} == {"infoway"} and any(i.symbol == "7203" for i in jp)
        tw = db.scalars(select(M.Instrument).where(M.Instrument.market_id == "TW", M.Instrument.delisted_on.is_(None))).all()
        assert tw and all(i.license_status == "sample" for i in tw)
