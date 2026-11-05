import io
import json
import urllib.error
import urllib.parse
from datetime import date, timedelta

import pytest

from app.providers.base import ProviderError
from app.providers.jquants import JQuantsJapanProvider, jpx_holidays


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_opener(pages, calls):
    def opener(req, timeout=0):
        calls.append((dict(req.header_items()), dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(req.full_url).query))))
        page = pages[len(calls) - 1]
        if isinstance(page, int):
            raise urllib.error.HTTPError(req.full_url, page, "err", {}, io.BytesIO(b'{"message": "x"}'))
        return FakeResp(json.dumps(page).encode())
    return opener


def row(d, c, f=1.0):
    return {"Date": d.isoformat(), "Code": "72030", "O": c, "H": c + 1, "L": c - 1, "C": c, "Vo": 1000, "AdjFactor": f}


def test_daily_bars_parses_sends_key_and_5_digit_code():
    calls = []
    page = {"data": [row(date(2026, 11, 2), 2990), row(date(2026, 11, 4), 3000)]}
    p = JQuantsJapanProvider("k", opener=make_opener([page], calls), sleep=lambda s: None)
    out = p.daily_bars("JP", "7203", date(2026, 11, 2), date(2026, 11, 4))
    assert [b.date for b in out] == [date(2026, 11, 2), date(2026, 11, 4)] and out[-1].close == 3000.0
    headers, q = calls[0]
    assert headers.get("X-api-key") == "k"
    assert q == {"code": "72030", "from": "20261102", "to": "20261104"}
    assert p.corporate_actions("JP", "7203") == []


def test_pagination_and_split_becomes_corporate_action():
    calls = []
    pages = [{"data": [row(date(2026, 10, 1), 100)], "pagination_key": "abc"},
             {"data": [row(date(2026, 10, 2), 50, 0.5)]}]
    p = JQuantsJapanProvider("k", opener=make_opener(pages, calls), sleep=lambda s: None)
    out = p.daily_bars("JP", "7203", date(2026, 10, 1), date(2026, 10, 2))
    assert len(out) == 2 and calls[1][1]["pagination_key"] == "abc"
    acts = p.corporate_actions("JP", "7203")
    assert len(acts) == 1 and acts[0].effective_date == date(2026, 10, 2) and acts[0].price_factor == 0.5


def test_history_clamped_to_plan_and_free_delay():
    calls = []
    p = JQuantsJapanProvider("k", plan="free", opener=make_opener([{"data": [row(date(2026, 1, 5), 1)]}], calls), sleep=lambda s: None)
    end = date.today()
    p.daily_bars("JP", "7203", end - timedelta(days=3650), end)
    q = calls[0][1]
    assert q["to"] == (end - timedelta(days=84)).strftime("%Y%m%d")
    assert q["from"] >= (end - timedelta(days=84 + 365 * 2)).strftime("%Y%m%d")
    assert p.min_interval > 12  # 每分鐘 5 次


def test_unknown_code_and_400_are_per_symbol_errors():
    p = JQuantsJapanProvider("k", opener=make_opener([{"data": []}], []), sleep=lambda s: None)
    with pytest.raises(ProviderError):
        p.daily_bars("JP", "9999", date(2026, 1, 1), date(2026, 11, 4))
    p = JQuantsJapanProvider("k", opener=make_opener([400], []), sleep=lambda s: None)
    with pytest.raises(ProviderError):
        p.daily_bars("JP", "7203", date(2026, 1, 1), date(2026, 11, 4))
    # 只抓幾天且沒有新資料是正常的
    p = JQuantsJapanProvider("k", opener=make_opener([{"data": []}], []), sleep=lambda s: None)
    assert p.daily_bars("JP", "7203", date(2026, 11, 3), date(2026, 11, 4)) == []


def test_rate_limit_waits_and_retries_once():
    slept = []
    p = JQuantsJapanProvider("k", opener=make_opener([429, {"data": [row(date(2026, 11, 4), 1)]}], []), sleep=slept.append)
    assert len(p.daily_bars("JP", "7203", date(2026, 11, 4), date(2026, 11, 4))) == 1
    assert 60 in slept
    p = JQuantsJapanProvider("k", opener=make_opener([401], []), sleep=lambda s: None)
    with pytest.raises(RuntimeError):  # 金鑰錯誤：整個市場作業失敗
        p.daily_bars("JP", "7203", date(2026, 11, 4), date(2026, 11, 4))


def test_jpx_calendar_skips_holidays():
    hol = jpx_holidays(date(2026, 1, 1), date(2026, 12, 31))
    assert date(2026, 1, 2) in hol and date(2026, 12, 31) in hol and date(2026, 11, 3) in hol
    sess = JQuantsJapanProvider("k").sessions("JP", date(2026, 11, 2), date(2026, 11, 6))
    assert [s.local_date for s in sess] == [date(2026, 11, 2), date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)]


def test_switching_sources_keeps_imported_and_rebackfills():
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.db import models as M
    from app.config import settings
    from app.seed import seed
    eng = create_engine("sqlite://")
    M.Base.metadata.create_all(eng)
    with sessionmaker(bind=eng)() as db:
        seed(db)
        # 先前來源（例如 Infoway）留下的股票與日線，含一檔使用者匯入的清單外股票
        old = M.Instrument(market_id="JP", mic="XTKS", symbol="1301", name="極洋", currency="JPY", license_status="infoway")
        db.add(old)
        db.flush()
        db.add(M.DailyBarRow(instrument_id=old.id, date=date(2026, 11, 4), source="infoway", revision=1,
                             open=1, high=1, low=1, close=1, volume=1))
        db.flush()
        object.__setattr__(settings, "jquants_api_key", "k")
        try:
            seed(db)
            db.flush()
        finally:
            object.__setattr__(settings, "jquants_api_key", "")
        jp = db.scalars(select(M.Instrument).where(M.Instrument.market_id == "JP", M.Instrument.delisted_on.is_(None))).all()
        assert {i.license_status for i in jp} == {"jquants"}
        assert any(i.symbol == "7203" for i in jp) and any(i.symbol == "1301" for i in jp)
        assert not any(i.symbol.startswith("J0") for i in jp)  # 示意股下架
        assert db.scalars(select(M.DailyBarRow).where(M.DailyBarRow.instrument_id == old.id)).all() == []
        tw = db.scalars(select(M.Instrument).where(M.Instrument.market_id == "TW", M.Instrument.delisted_on.is_(None))).all()
        assert tw and all(i.license_status == "sample" for i in tw)


def test_missing_key_does_not_delist_real_stocks():
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.db import models as M
    from app.seed import seed
    eng = create_engine("sqlite://")
    M.Base.metadata.create_all(eng)
    with sessionmaker(bind=eng)() as db:
        db.add(M.Market(id="JP", mic="XTKS", timezone="Asia/Tokyo", currency="JPY", close_time="15:30"))
        db.add(M.Instrument(market_id="JP", mic="XTKS", symbol="1301", name="極洋", currency="JPY", license_status="jquants"))
        db.flush()
        seed(db)  # 沒有金鑰：日股退回示意資料
        db.flush()
        inst = db.scalar(select(M.Instrument).where(M.Instrument.symbol == "1301"))
        assert inst.delisted_on is None and inst.license_status == "jquants"
