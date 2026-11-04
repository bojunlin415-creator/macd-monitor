from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models as M
from app.api import deps
from app.main import app
from app.seed import seed


@pytest.fixture
def env(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    M.Base.metadata.create_all(eng)
    Factory = sessionmaker(bind=eng, expire_on_commit=False)
    monkeypatch.setattr(deps, "SessionLocal", Factory)
    with Factory() as db:
        seed(db, "admin@example.com")
        db.add(M.User(email="member@example.com"))
        db.add(M.User(email="other@example.com"))
        db.commit()
    return Factory


def login(email):
    c = TestClient(app)
    r = c.post("/api/v1/auth/email-link", json={"email": email})
    link = r.json()["data"]["dev_link"]
    token = link.split("token=")[1]
    r = c.post("/api/v1/auth/verify", json={"token": token})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["data"]["csrf_token"]
    # 用後即失效
    assert c.post("/api/v1/auth/verify", json={"token": token}).status_code == 400
    return c


def test_uninvited_gets_same_message_and_no_link(env):
    c = TestClient(app)
    r = c.post("/api/v1/auth/email-link", json={"email": "stranger@example.com"})
    assert r.status_code == 200 and r.json()["data"]["dev_link"] is None
    assert c.get("/api/v1/overview").status_code == 401


def test_watchlist_quota_and_isolation(env):
    c = login("member@example.com")
    inst = c.get("/api/v1/instruments", params={"market": "TW"}).json()["data"]
    r = c.post("/api/v1/watchlist", json={"instrument_ids": [inst[0]["id"], inst[0]["id"], inst[1]["id"]]})
    assert r.json()["data"]["added"] == 2
    wl = c.get("/api/v1/watchlist").json()["data"]
    assert wl["used"] == 2
    # 其他成員看不到也刪不掉
    o = login("other@example.com")
    assert o.get("/api/v1/watchlist").json()["data"]["used"] == 0
    r = o.delete(f"/api/v1/watchlist/{wl['items'][0]['id']}")
    assert r.status_code == 404 and r.json()["error"]["message"].startswith("找不到")


def test_quota_limit_200(env):
    with env() as db:
        u = db.scalar(select(M.User).where(M.User.email == "member@example.com"))
        for i in range(199):
            inst = M.Instrument(market_id="TW", mic="XTAI", symbol=f"Z{i:03d}", name="測試", currency="TWD")
            db.add(inst)
            db.flush()
            db.add(M.WatchlistItem(user_id=u.id, instrument_id=inst.id))
        db.commit()
    c = login("member@example.com")
    inst = c.get("/api/v1/instruments", params={"market": "US"}).json()["data"]
    r = c.post("/api/v1/watchlist", json={"instrument_ids": [inst[0]["id"], inst[1]["id"]]})
    assert r.status_code == 409 and "請再取消勾選 1 檔" in r.json()["error"]["message"]


def test_csrf_required(env):
    c = login("member@example.com")
    del c.headers["X-CSRF-Token"]
    assert c.post("/api/v1/watchlist", json={"instrument_ids": ["x"]}).status_code == 403


def test_csv_preview_commit(env):
    c = login("member@example.com")
    csv = "﻿market,symbol,group,fast,slow,signal\nTW,T001,半導體,,,\nTW,T001,,,,\nUS,U001,,26,12,9\nXX,1,,,,\nJP,J003,日股,10,20,5\n"
    r = c.post("/api/v1/watchlist/import-preview", files={"file": ("a.csv", csv.encode(), "text/csv")})
    d = r.json()["data"]
    kinds = [row["kind"] for row in d["rows"]]
    assert kinds == ["ok", "duplicate", "error", "error", "ok"]
    assert "慢線週數必須大於快線週數" in d["rows"][2]["reason"]
    r = c.post("/api/v1/watchlist/import-commit", json={"preview_id": d["preview_id"], "selected_lines": [2, 6]})
    assert r.json()["data"]["added"] == 2


def test_rules_validation_and_conflict(env):
    c = login("member@example.com")
    r = c.put("/api/v1/rules/global", json={"fast": 26, "slow": 12, "signal": 9})
    assert r.status_code == 422 and r.json()["error"]["field_errors"]["slow"]
    r = c.put("/api/v1/rules/global", json={"fast": 10, "slow": 20, "signal": 5, "version": 0})
    assert r.status_code == 200 and "當週生效" in r.json()["data"]["message"]
    r = c.put("/api/v1/rules/global", json={"fast": 10, "slow": 20, "signal": 5, "version": 0})
    assert r.status_code == 409


def test_prefs_cannot_mute_mandatory(env):
    c = login("member@example.com")
    r = c.put("/api/v1/me/preferences", json={"muted_codes": ["POS_WEAK"]})
    assert r.status_code == 422
    r = c.put("/api/v1/me/preferences", json={"muted_codes": ["CROSS"], "theme": "dark", "merge_threshold": 7})
    d = r.json()["data"]
    assert d["notify"]["muted_codes"] == ["CROSS"] and d["theme"] == "dark"


def test_positions_idempotent_and_oversell(env):
    c = login("member@example.com")
    iid = c.get("/api/v1/instruments", params={"market": "TW"}).json()["data"][0]["id"]
    body = {"instrument_id": iid, "type": "buy", "date": "2026-01-05", "qty": 10, "price": 100, "fee": 10}
    r1 = c.post("/api/v1/positions/transactions", json=body, headers={"Idempotency-Key": "k1"})
    r2 = c.post("/api/v1/positions/transactions", json=body, headers={"Idempotency-Key": "k1"})
    assert r2.json()["data"]["duplicate"] and r1.json()["data"]["position"]["quantity"] == 10
    c.post("/api/v1/positions/transactions", json={**body, "price": 120}, headers={"Idempotency-Key": "k2"})
    pos = c.post("/api/v1/positions/transactions", json={**body, "type": "sell", "qty": 5, "price": 130, "fee": 5},
                 headers={"Idempotency-Key": "k3"}).json()["data"]["position"]
    assert pos["avg_cost"] == pytest.approx(111) and pos["realized_pnl"] == pytest.approx(90)
    r = c.post("/api/v1/positions/transactions", json={**body, "type": "sell", "qty": 99}, headers={"Idempotency-Key": "k4"})
    assert r.status_code == 422 and r.json()["error"]["message"] == "賣出數量超過持有數量。"
    # A 查 B 持股：B 看不到
    o = login("other@example.com")
    assert o.get("/api/v1/positions").json()["data"]["items"] == []


def test_admin_flow_and_overview(env):
    a = login("admin@example.com")
    m = login("member@example.com")
    assert m.get("/api/v1/admin/jobs").status_code == 404
    inst = m.get("/api/v1/instruments", params={"market": "TW"}).json()["data"]
    m.post("/api/v1/watchlist", json={"instrument_ids": [i["id"] for i in inst]})
    r = a.post("/api/v1/admin/jobs/run", json={"market": "TW", "as_of": "2026-11-04"})
    assert r.json()["data"]["job"]["status"] == "done", r.text
    ov = m.get("/api/v1/overview").json()["data"]
    assert ov["week"]["watched"] == 8
    assert {x["id"] for x in ov["markets"]} == {"TW", "US", "JP"}
    w = m.get(f"/api/v1/instruments/{inst[0]['id']}/weekly").json()["data"]
    assert len(w["weeks"]) == 156 and w["weeks"][-1]["status"] == "provisional"
    sig = m.get("/api/v1/signals", params={"primary_only": "false"}).json()
    for e in sig["data"][:3]:
        d = m.get(f"/api/v1/signals/{e['id']}").json()["data"]
        assert d["conditions"] and d["versions"]["engine"]
    r = a.post("/api/v1/admin/invitations", json={"email": "new@example.com"})
    tok = r.json()["data"]["dev_link"].split("token=")[1]
    n = TestClient(app)
    assert n.post("/api/v1/auth/accept-invite", json={"token": tok}).status_code == 200
    assert TestClient(app).post("/api/v1/auth/accept-invite", json={"token": tok}).status_code == 400
