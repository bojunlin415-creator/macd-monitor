import io
import json
import urllib.error
import urllib.parse
from datetime import date

import pytest

from app.providers.base import ProviderError, QuotaExhausted
from app.providers.finmind import FinMindTaiwanProvider


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_opener(routes, calls):
    """routes：dataset → 回應（dict、HTTP 狀態碼或例外）。"""
    def opener(req, timeout=0):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(req.full_url).query))
        calls.append((dict(req.header_items()), q))
        r = routes[q["dataset"]]
        if isinstance(r, int):
            raise urllib.error.HTTPError(req.full_url, r, "err", {}, io.BytesIO(b"{}"))
        return FakeResp(json.dumps(r).encode())
    return opener


def ok(data):
    return {"msg": "success", "status": 200, "data": data}


def price(d, c):
    return {"date": d, "stock_id": "2330", "open": c, "max": c + 1, "min": c - 1, "close": c, "Trading_Volume": 1000}


def P(routes, calls=None):
    return FinMindTaiwanProvider("tok", opener=make_opener(routes, [] if calls is None else calls), sleep=lambda s: None)


SPLIT = {"date": "2025-06-18", "stock_id": "0050", "type": "分割", "before_price": 188.65, "after_price": 47.16}
PAR = {"date": "2024-11-11", "stock_id": "8476", "before_close": 58.8, "after_ref_close": 29.4}


def test_daily_bars_and_dividend_adjustment():
    calls = []
    p = P({"TaiwanStockPrice": ok([price("2026-06-10", 1000), price("2026-06-11", 990), {**price("2026-06-12", 0)}]),
           "TaiwanStockDividendResult": ok([{"date": "2026-06-11", "stock_id": "2330", "before_price": 1000,
                                             "reference_price": 995, "after_price": 990}]),
           "TaiwanStockCapitalReductionReferencePrice": ok([]),
           "TaiwanStockSplitPrice": ok([SPLIT]), "TaiwanStockParValueChange": ok([PAR])}, calls)
    bars = p.daily_bars("TW", "2330", date(2026, 6, 10), date(2026, 6, 12))
    assert [b.date for b in bars] == [date(2026, 6, 10), date(2026, 6, 11)]  # 無成交日略過
    headers, q = calls[0]
    assert headers.get("Authorization") == "Bearer tok" and q["data_id"] == "2330" and q["start_date"] == "2026-06-10"
    acts = p.corporate_actions("TW", "2330")
    assert len(acts) == 1 and acts[0].effective_date == date(2026, 6, 11) and acts[0].price_factor == pytest.approx(0.995)
    div = next(q for _, q in calls if q["dataset"] == "TaiwanStockDividendResult")
    assert div == {"dataset": "TaiwanStockDividendResult", "data_id": "2330", "start_date": "2026-06-10", "end_date": "2026-06-11"}
    assert p.corporate_actions("TW", "2330") == []  # 沒有新日線時不再查除權息


def test_split_and_par_value_from_market_wide_list():
    calls = []
    p = P({"TaiwanStockSplitPrice": ok([SPLIT]), "TaiwanStockParValueChange": ok([PAR])}, calls)
    a = p.corporate_actions("TW", "0050")
    assert [(x.effective_date, x.kind) for x in a] == [(date(2025, 6, 18), "split")]
    assert a[0].price_factor == pytest.approx(47.16 / 188.65)
    assert p.corporate_actions("TW", "8476")[0].price_factor == pytest.approx(0.5)
    assert len(calls) == 2 and all("data_id" not in q for _, q in calls)  # 整個市場只查一次


def test_split_falls_back_to_per_symbol_and_capital_reduction():
    calls = []
    routes = {"TaiwanStockSplitPrice": 400, "TaiwanStockParValueChange": ok([]),
              "TaiwanStockPrice": ok([price("2026-06-10", 20)]), "TaiwanStockDividendResult": ok([]),
              "TaiwanStockCapitalReductionReferencePrice": ok([{"date": "2026-06-10", "stock_id": "2327",
                                                               "ClosingPriceonTheLastTradingDay": 22.05,
                                                               "PostReductionReferencePrice": 49.82}])}
    p = P(routes, calls)
    p.daily_bars("TW", "2327", date(2026, 6, 10), date(2026, 6, 10))
    a = p.corporate_actions("TW", "2327")  # 分割查詢失敗不影響這檔
    assert [(x.kind, round(x.price_factor, 3)) for x in a] == [("capital_reduction", round(49.82 / 22.05, 3))]
    assert any(q.get("data_id") == "2327" and q["dataset"] == "TaiwanStockSplitPrice" for _, q in calls)


def test_unknown_symbol_and_quota():
    with pytest.raises(ProviderError):
        P({"TaiwanStockPrice": ok([])}).daily_bars("TW", "9999", date(2025, 1, 1), date(2026, 11, 4))
    assert P({"TaiwanStockPrice": ok([])}).daily_bars("TW", "2330", date(2026, 11, 3), date(2026, 11, 4)) == []
    with pytest.raises(QuotaExhausted):
        P({"TaiwanStockPrice": 402}).daily_bars("TW", "2330", date(2026, 11, 3), date(2026, 11, 4))


def test_instruments_listed_and_otc_dedup_and_filter():
    p = P({"TaiwanStockInfo": ok([
        {"stock_id": "2330", "stock_name": "台積電", "type": "twse", "industry_category": "半導體業"},
        {"stock_id": "2330", "stock_name": "台積電", "type": "twse", "industry_category": "電子工業"},
        {"stock_id": "6488", "stock_name": "環球晶", "type": "tpex"},
        {"stock_id": "0050", "stock_name": "元大台灣50", "type": "twse"},
        {"stock_id": "00878", "stock_name": "國泰永續高股息", "type": "twse"},
        {"stock_id": "TAIEX", "stock_name": "加權指數", "type": "twse"},
        {"stock_id": "2330", "stock_name": "x", "type": "emerging"},
    ])})
    got = {(i.symbol, i.mic, i.name) for i in p.instruments("TW")}
    assert got == {("2330", "XTAI", "台積電"), ("6488", "ROCO", "環球晶"), ("0050", "XTAI", "元大台灣50"),
                   ("00878", "XTAI", "國泰永續高股息")}


def test_sessions_from_trading_date_table_then_holidays():
    p = P({"TaiwanStockTradingDate": ok([{"date": "2026-11-02"}, {"date": "2026-11-03"}, {"date": "2026-11-04"}])})
    sess = p.sessions("TW", date(2026, 11, 2), date(2026, 11, 10))
    assert [s.local_date for s in sess] == [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5),
                                            date(2026, 11, 6), date(2026, 11, 9), date(2026, 11, 10)]
    fallback = P({"TaiwanStockTradingDate": 500}).sessions("TW", date(2026, 10, 5), date(2026, 10, 12))
    assert date(2026, 10, 10) not in [s.local_date for s in fallback]


def test_run_market_syncs_and_defers_on_quota(monkeypatch):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.db import models as M
    from app.seed import seed
    from app.workers.pipeline import run_market
    eng = create_engine("sqlite://")
    M.Base.metadata.create_all(eng)
    with sessionmaker(bind=eng)() as db:
        seed(db, "a@example.com")
        db.flush()
        u = db.scalar(select(M.User))
        calls = {"n": 0}

        def opener(req, timeout=0):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(req.full_url).query))
            if q["dataset"] == "TaiwanStockInfo":
                return FakeResp(json.dumps(ok([{"stock_id": "2330", "stock_name": "台積電", "type": "twse"},
                                               {"stock_id": "2317", "stock_name": "鴻海", "type": "twse"}])).encode())
            if q["dataset"] == "TaiwanStockTradingDate":
                return FakeResp(json.dumps(ok([])).encode())
            if q["dataset"] == "TaiwanStockPrice":
                calls["n"] += 1
                if calls["n"] > 1:
                    raise urllib.error.HTTPError(req.full_url, 402, "quota", {}, io.BytesIO(b"{}"))
                return FakeResp(json.dumps(ok([price("2026-11-03", 1000), price("2026-11-04", 1010)])).encode())
            return FakeResp(json.dumps(ok([])).encode())

        p = FinMindTaiwanProvider("tok", opener=opener, sleep=lambda s: None)
        from app.workers.pipeline import sync_instruments
        sync_instruments(db, p, "TW")
        db.flush()
        for sym in ("2330", "2317"):
            inst = db.scalar(select(M.Instrument).where(M.Instrument.symbol == sym))
            db.add(M.WatchlistItem(user_id=u.id, instrument_id=inst.id))
        db.flush()
        job = run_market(db, p, "TW", date(2026, 11, 4))
        assert job.status == "done", job.error
        ing = job.detail["ingest"]
        assert ing["bars"] == 2 and ing["deferred"] == 1
        tw = db.scalars(select(M.Instrument).where(M.Instrument.market_id == "TW", M.Instrument.delisted_on.is_(None))).all()
        assert {i.symbol for i in tw} == {"2330", "2317"}  # 示意股下架
