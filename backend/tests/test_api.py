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
    from app import main
    main._hits.clear()  # 每個測試重新計算流量限制
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


def test_uninvited_gets_same_message_and_no_link(env, monkeypatch):
    monkeypatch.setenv("SIGNUP_MODE", "invite")
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
        db.add(M.Membership(user_id=u.id, plan="pro"))
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


def test_invite_cannot_log_into_existing_account(env):
    a = login("admin@example.com")
    r = a.post("/api/v1/admin/invitations", json={"email": "member@example.com"})
    tok = r.json()["data"]["dev_link"].split("token=")[1]
    r = TestClient(app).post("/api/v1/auth/accept-invite", json={"token": tok})
    assert r.status_code == 409


def test_cron_and_bootstrap_require_secret(env, monkeypatch):
    from app import config
    c = TestClient(app)
    assert c.get("/api/v1/cron/dispatch").status_code == 404
    object.__setattr__(config.settings, "cron_secret", "s3cret")
    request_cleanup = lambda: object.__setattr__(config.settings, "cron_secret", "")  # noqa: E731
    monkeypatch.setattr("app.api.cron.ok", __import__("app.api.deps", fromlist=["ok"]).ok)  # 保持匯入

    assert c.get("/api/v1/cron/dispatch", headers={"Authorization": "Bearer nope"}).status_code == 404
    r = c.post("/api/v1/auth/bootstrap-link", json={"email": "member@example.com"}, headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 404  # 只能為管理員產生
    r = c.post("/api/v1/auth/bootstrap-link", json={"email": "admin@example.com"}, headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200 and "token=" in r.json()["data"]["link"]
    request_cleanup()


# ---- Google 登入 ----
def _google(monkeypatch, claims_override=None):
    from app.api import google
    from app.config import settings
    object.__setattr__(settings, "google_client_id", "cid")
    object.__setattr__(settings, "google_client_secret", "csec")
    c = TestClient(app, follow_redirects=False)
    r = c.get("/api/v1/auth/google/start")
    assert r.status_code == 302 and "accounts.google.com" in r.headers["location"]
    state, nonce = c.cookies.get("g_state").split(".")
    holder = {}

    def fake_exchange(code):
        claims = {"iss": "https://accounts.google.com", "aud": "cid", "exp": 9e12, "nonce": nonce,
                  "email_verified": True, "email": holder["email"]}
        claims.update(claims_override or {})
        return claims
    monkeypatch.setattr(google, "exchange_code", fake_exchange)

    def finish(email, st=None):
        holder["email"] = email
        return c.get(f"/api/v1/auth/google/callback?code=x&state={st or state}")
    return c, finish


@pytest.fixture
def google_off():
    yield
    from app.config import settings
    object.__setattr__(settings, "google_client_id", "")
    object.__setattr__(settings, "google_client_secret", "")


def test_google_login_existing_member(env, monkeypatch, google_off):
    c, finish = _google(monkeypatch)
    r = finish("Member@example.com")
    assert r.status_code == 302 and r.headers["location"] == "/"
    assert c.get("/api/v1/me").json()["data"]["email"] == "member@example.com"


def test_google_login_rejects_uninvited_and_bad_state(env, monkeypatch, google_off):
    monkeypatch.setenv("SIGNUP_MODE", "invite")
    _c, finish = _google(monkeypatch)
    assert finish("stranger@example.com").headers["location"] == "/login?error=not_invited"
    _c, finish = _google(monkeypatch)
    assert finish("member@example.com", st="forged").headers["location"] == "/login?error=google_failed"
    with env() as db:
        assert db.scalar(select(M.User).where(M.User.email == "stranger@example.com")) is None


def test_google_login_unverified_email_rejected(env, monkeypatch, google_off):
    _c, finish = _google(monkeypatch, {"email_verified": False})
    assert finish("member@example.com").headers["location"] == "/login?error=google_failed"


def test_google_login_accepts_invitation(env, monkeypatch, google_off):
    admin = login("admin@example.com")
    assert admin.post("/api/v1/admin/invitations", json={"email": "friend@example.com"}).status_code == 200
    c, finish = _google(monkeypatch)
    assert finish("friend@example.com").headers["location"] == "/"
    assert c.get("/api/v1/me").json()["data"]["email"] == "friend@example.com"
    # 邀請已用掉，帳號存在後仍可再次以 Google 登入
    c2, finish2 = _google(monkeypatch)
    assert finish2("friend@example.com").headers["location"] == "/"


def test_google_disabled_by_default(env):
    r = TestClient(app).get("/api/v1/auth/providers")
    assert r.json()["data"] == {"google": False, "signup_mode": "open"}


# ---- LINE 綁定 Webhook ----
def test_line_webhook_binds_and_replies(env, monkeypatch):
    import base64, hashlib, hmac, json
    from app.config import settings
    from app.workers import runner
    object.__setattr__(settings, "line_channel_secret", "chs")
    try:
        c = login("member@example.com")
        code = c.post("/api/v1/line/link").json()["data"]["code"]
        sent = []

        class Fake:
            def reply(self, token, text):
                sent.append((token, text)); return True
        monkeypatch.setattr(runner, "line_client", lambda: Fake())

        def post(events):
            raw = json.dumps({"events": events}).encode()
            sig = base64.b64encode(hmac.new(b"chs", raw, hashlib.sha256).digest()).decode()
            return TestClient(app).post("/api/v1/webhooks/line", content=raw, headers={"X-Line-Signature": sig})

        assert post([]).status_code == 200  # LINE 後台的 Verify
        ev = {"type": "message", "webhookEventId": "e1", "replyToken": "rt1", "source": {"type": "user", "userId": "U123"},
              "message": {"type": "text", "text": f"綁定 {code}"}}
        assert post([ev]).status_code == 200
        assert sent and sent[0][0] == "rt1" and "綁定完成" in sent[0][1]
        assert c.get("/api/v1/line/status").json()["data"]["status"] == "active"
        bad = TestClient(app).post("/api/v1/webhooks/line", content=b'{"events":[]}', headers={"X-Line-Signature": "x"})
        assert bad.status_code == 401
    finally:
        object.__setattr__(settings, "line_channel_secret", "")


# ---- 會員制：自行註冊、核准、方案額度 ----
def test_open_signup_by_email_creates_free_member(env):
    c = login("newbie@example.com")
    me = c.get("/api/v1/me").json()["data"]
    assert me["email"] == "newbie@example.com" and me["role"] == "member" and me["plan"]["id"] == "free"
    m = c.get("/api/v1/me/membership").json()["data"]
    assert m["plan"]["watch_limit"] == 50 and m["payments_enabled"] is False
    # 免費會員也能綁定 LINE
    assert c.post("/api/v1/line/link").status_code in (200, 503)


def test_free_plan_watch_limit(env):
    with env() as db:
        u = db.scalar(select(M.User).where(M.User.email == "member@example.com"))
        for i in range(49):
            inst = M.Instrument(market_id="TW", mic="XTAI", symbol=f"Z{i:03d}", name="測試", currency="TWD")
            db.add(inst)
            db.flush()
            db.add(M.WatchlistItem(user_id=u.id, instrument_id=inst.id))
        db.commit()
    c = login("member@example.com")
    inst = c.get("/api/v1/instruments", params={"market": "US"}).json()["data"]
    r = c.post("/api/v1/watchlist", json={"instrument_ids": [inst[0]["id"], inst[1]["id"]]})
    assert r.status_code == 409 and "免費會員的自選股上限是 50 檔" in r.json()["error"]["message"]


def test_approval_mode_requires_admin(env, monkeypatch):
    monkeypatch.setenv("SIGNUP_MODE", "approval")
    c = TestClient(app)
    link = c.post("/api/v1/auth/email-link", json={"email": "applicant@example.com"}).json()["data"]["dev_link"]
    r = c.post("/api/v1/auth/verify", json={"token": link.split("token=")[1]})
    assert r.status_code == 403 and r.json()["error"]["code"] == "pending_approval"
    admin = login("admin@example.com")
    row = next(u for u in admin.get("/api/v1/admin/members").json()["data"] if u["email"] == "applicant@example.com")
    assert row["status"] == "pending"
    assert admin.put(f"/api/v1/admin/members/{row['id']}/status", json={"status": "active"}).status_code == 200
    login("applicant@example.com")


def test_google_open_signup(env, monkeypatch, google_off):
    c, finish = _google(monkeypatch)
    assert finish("gnew@example.com").headers["location"] == "/"
    assert c.get("/api/v1/me").json()["data"]["plan"]["id"] == "free"


def test_admin_sets_plan_and_expiry_falls_back(env):
    from datetime import datetime, timezone
    admin = login("admin@example.com")
    row = next(u for u in admin.get("/api/v1/admin/members").json()["data"] if u["email"] == "member@example.com")
    assert row["plan"] == "free"
    assert admin.put(f"/api/v1/admin/members/{row['id']}/plan", json={"plan": "nope"}).status_code == 422
    assert admin.put(f"/api/v1/admin/members/{row['id']}/plan", json={"plan": "pro"}).status_code == 200
    c = login("member@example.com")
    assert c.get("/api/v1/me/membership").json()["data"]["plan"]["id"] == "pro"
    with env() as db:
        db.get(M.Membership, row["id"]).current_period_end = datetime(2020, 1, 1, tzinfo=timezone.utc)
        db.commit()
    assert c.get("/api/v1/me/membership").json()["data"]["plan"]["id"] == "free"
    # 一般成員不能改方案
    assert c.put(f"/api/v1/admin/members/{row['id']}/plan", json={"plan": "pro"}).status_code == 404
