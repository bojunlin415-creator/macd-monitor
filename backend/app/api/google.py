"""Google 登入（OpenID Connect 授權碼流程）。

僅限受邀成員：Google 帳號的 Email 必須已是啟用中的成員，或有一筆有效的邀請；
否則一律拒絕，不會自動建立帳號。
"""
from __future__ import annotations

import base64
import json
import logging
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import models as M
from app.api.deps import SESSION_COOKIE, _aware, audit, create_session, get_db, h

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/google", tags=["auth"])

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
STATE_COOKIE = "g_state"


def enabled() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def _redirect_uri() -> str:
    return f"{settings.app_base_url}/api/v1/auth/google/callback"


def _fail(code: str) -> RedirectResponse:
    resp = RedirectResponse(f"/login?error={code}", status_code=302)
    resp.delete_cookie(STATE_COOKIE, path="/api/v1/auth/google")
    return resp


def exchange_code(code: str) -> dict:
    """以授權碼向 Google 換取 id_token；回傳 id_token 的內容。

    id_token 是透過 TLS 直接從 Google 權杖端點取得，依 OIDC 規範可不另驗簽章，
    但仍檢查發行者、受眾與到期時間。
    """
    data = urllib.parse.urlencode({
        "code": code, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
        "redirect_uri": _redirect_uri(), "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=10) as r:
        tok = json.loads(r.read())
    payload = tok["id_token"].split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


@router.get("/start")
def start():
    if not enabled():
        return _fail("google_disabled")
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    q = urllib.parse.urlencode({
        "client_id": settings.google_client_id, "redirect_uri": _redirect_uri(), "response_type": "code",
        "scope": "openid email", "state": state, "nonce": nonce, "prompt": "select_account",
    })
    resp = RedirectResponse(f"{AUTH_URL}?{q}", status_code=302)
    resp.set_cookie(STATE_COOKIE, f"{state}.{nonce}", max_age=600, httponly=True, secure=not settings.is_dev,
                    samesite="lax", path="/api/v1/auth/google")
    return resp


@router.get("/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = "", db: Session = Depends(get_db)):
    if error or not code:
        return _fail("google_cancelled")
    saved = request.cookies.get(STATE_COOKIE, "")
    exp_state, _, exp_nonce = saved.partition(".")
    if not enabled() or not saved or not secrets.compare_digest(state, exp_state):
        return _fail("google_failed")
    try:
        claims = exchange_code(code)
    except Exception:
        log.exception("Google 權杖交換失敗")
        return _fail("google_failed")
    now = datetime.now(timezone.utc).timestamp()
    if (claims.get("iss") not in ISSUERS or claims.get("aud") != settings.google_client_id
            or claims.get("exp", 0) < now or claims.get("nonce") != exp_nonce
            or not claims.get("email_verified") or not claims.get("email")):
        return _fail("google_failed")

    email = claims["email"].lower()
    user = db.scalar(select(M.User).where(func.lower(M.User.email) == email))
    if user is None:
        inv = db.scalar(select(M.Invitation).where(func.lower(M.Invitation.email) == email, M.Invitation.used_at.is_(None))
                        .order_by(M.Invitation.expires_at.desc()))
        if inv is None or _aware(inv.expires_at) < datetime.now(timezone.utc):
            return _fail("not_invited")
        inv.used_at = datetime.now(timezone.utc)
        user = M.User(email=email)
        db.add(user)
        db.flush()
        audit(db, user, "accept_invite", f"user:{user.id}", {"method": "google"})
    elif user.status != "active":
        return _fail("not_invited")

    sid, _csrf = create_session(db, user)
    audit(db, user, "login", f"user:{user.id}", {"method": "google"})
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(SESSION_COOKIE, sid, httponly=True, secure=not settings.is_dev, samesite="lax",
                    max_age=7 * 24 * 3600, path="/")
    resp.delete_cookie(STATE_COOKIE, path="/api/v1/auth/google")
    return resp
