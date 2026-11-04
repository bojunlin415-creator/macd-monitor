"""登入：Email 一次性連結（15 分鐘、用後失效）與邀請（7 天、單次）。

Google OIDC 與管理員 MFA 為下一階段（見 docs/MVP計畫.md）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import models as M
from app.api.deps import (
    SESSION_COOKIE, ApiError, _aware, admin_user, audit, create_session, current_user, get_db, h, new_token, ok,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

LOGIN_TTL = timedelta(minutes=15)
INVITE_TTL = timedelta(days=7)


class EmailIn(BaseModel):
    email: EmailStr


class TokenIn(BaseModel):
    token: str


def _set_cookie(resp: Response, sid: str) -> None:
    resp.set_cookie(SESSION_COOKIE, sid, httponly=True, secure=not settings.is_dev, samesite="lax",
                    max_age=7 * 24 * 3600, path="/")


def _send_email(to: str, subject: str, body: str) -> None:
    from app.notify.email import send_email
    send_email(to, subject, body)


@router.post("/email-link")
def request_link(body: EmailIn, request: Request, db: Session = Depends(get_db)):
    email = body.email.lower()
    user = db.scalar(select(M.User).where(func.lower(M.User.email) == email))
    dev_link = None
    if user and user.status == "active":
        token = new_token()
        db.add(M.LoginToken(token_hash=h(token), user_id=user.id,
                            expires_at=datetime.now(timezone.utc) + LOGIN_TTL))
        link = f"{settings.app_base_url}/login/verify?token={token}"
        _send_email(email, "MACD 監測系統登入連結", f"請在 15 分鐘內點擊登入：{link}")
        dev_link = link if settings.is_dev else None
    # 不透露帳號是否存在
    return ok({"message": "若此 Email 已受邀，登入連結已寄出，15 分鐘內有效。", "dev_link": dev_link}, request)


@router.post("/verify")
def verify(body: TokenIn, request: Request, response: Response, db: Session = Depends(get_db)):
    row = db.get(M.LoginToken, h(body.token))
    now = datetime.now(timezone.utc)
    if row is None or row.used_at or _aware(row.expires_at) < now:
        raise ApiError(400, "invalid_link", "登入連結無效或已過期，請重新取得。")
    user = db.get(M.User, row.user_id)
    if user.status != "active":
        raise ApiError(400, "invalid_link", "登入連結無效或已過期，請重新取得。")
    row.used_at = now
    sid, csrf = create_session(db, user)
    _set_cookie(response, sid)
    audit(db, user, "login", f"user:{user.id}", {"method": "email"})
    return ok({"csrf_token": csrf}, request)


@router.post("/accept-invite")
def accept_invite(body: TokenIn, request: Request, response: Response, db: Session = Depends(get_db)):
    inv = db.scalar(select(M.Invitation).where(M.Invitation.token_hash == h(body.token)))
    now = datetime.now(timezone.utc)
    if inv is None or inv.used_at or _aware(inv.expires_at) < now:
        raise ApiError(400, "invalid_invite", "邀請連結無效或已過期，請聯絡管理員重新邀請。")
    user = db.scalar(select(M.User).where(func.lower(M.User.email) == inv.email.lower()))
    if user is not None:
        # 已有帳號不得以邀請連結登入，避免管理員藉邀請冒名登入
        raise ApiError(409, "already_member", "此 Email 已經是成員，請改用登入連結。")
    inv.used_at = now
    user = M.User(email=inv.email)
    db.add(user)
    db.flush()
    sid, csrf = create_session(db, user)
    _set_cookie(response, sid)
    audit(db, user, "accept_invite", f"user:{user.id}")
    return ok({"csrf_token": csrf}, request)


@router.post("/logout")
def logout(request: Request, response: Response, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(request.state.session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return ok({"message": "已登出。"}, request)


@router.get("/csrf")
def csrf(request: Request, user: M.User = Depends(current_user)):
    return ok({"csrf_token": request.state.session.csrf_token}, request)


admin_router = APIRouter(prefix="/admin", tags=["admin"])


class BootstrapIn(BaseModel):
    email: EmailStr


@router.post("/bootstrap-link")
def bootstrap_link(body: BootstrapIn, request: Request, db: Session = Depends(get_db)):
    """部署後首次登入：以 CRON_SECRET 驗證，只能為「管理員」產生一次性登入連結。

    系統還沒有任何管理員時，會以此 Email 建立第一位管理員。
    """
    import hmac
    auth = request.headers.get("Authorization", "")
    if not settings.cron_secret or not hmac.compare_digest(auth, f"Bearer {settings.cron_secret}"):
        raise ApiError(404, "not_found", "找不到這個頁面，或你沒有查看權限。")
    user = db.scalar(select(M.User).where(func.lower(M.User.email) == body.email.lower(), M.User.role == "admin"))
    if user is None and db.scalar(select(M.User).where(M.User.role == "admin")) is None:
        # 系統尚無管理員：以此 Email 建立第一位管理員
        user = M.User(email=body.email.lower(), display_name="管理員", role="admin")
        db.add(user)
        db.flush()
    if user is None or user.status != "active":
        raise ApiError(404, "not_found", "找不到這個頁面，或你沒有查看權限。")
    token = new_token()
    db.add(M.LoginToken(token_hash=h(token), user_id=user.id, expires_at=datetime.now(timezone.utc) + LOGIN_TTL))
    audit(db, None, "bootstrap_link", f"user:{user.id}")
    return ok({"link": f"{settings.app_base_url}/login/verify?token={token}"}, request)


@admin_router.post("/invitations")
def invite(body: EmailIn, request: Request, admin: M.User = Depends(admin_user), db: Session = Depends(get_db)):
    token = new_token()
    db.add(M.Invitation(email=body.email.lower(), token_hash=h(token), inviter_id=admin.id,
                        expires_at=datetime.now(timezone.utc) + INVITE_TTL))
    link = f"{settings.app_base_url}/invite?token={token}"
    _send_email(body.email, "你受邀使用 MACD 監測系統", f"請在 7 天內點擊接受邀請：{link}")
    audit(db, admin, "invite", f"email:{body.email.split('@')[-1]}")
    from app.notify.email import email_configured
    msg = "邀請已寄出，7 天內有效。" if email_configured() else "尚未設定寄信服務，請把下方連結傳給受邀者（7 天內有效、單次使用）。"
    return ok({"message": msg, "dev_link": link}, request)


@admin_router.get("/members")
def members(request: Request, admin: M.User = Depends(admin_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(M.User).order_by(M.User.created_at)).all()
    return ok([{"id": u.id, "email": u.email, "display_name": u.display_name, "role": u.role, "status": u.status,
                "created_at": u.created_at.isoformat()} for u in rows], request)


class StatusIn(BaseModel):
    status: str


@admin_router.put("/members/{user_id}/status")
def set_status(user_id: str, body: StatusIn, request: Request, admin: M.User = Depends(admin_user), db: Session = Depends(get_db)):
    if body.status not in ("active", "disabled"):
        raise ApiError(422, "invalid", "狀態只能是啟用或停用。")
    u = db.get(M.User, user_id)
    if u is None:
        raise ApiError(404, "not_found", "找不到這個頁面，或你沒有查看權限。")
    if u.id == admin.id:
        raise ApiError(422, "invalid", "不能停用自己的帳號。")
    u.status = body.status
    if body.status == "disabled":  # 停用立即阻止登入與新推播
        for s in db.scalars(select(M.WebSession).where(M.WebSession.user_id == u.id)):
            db.delete(s)
    audit(db, admin, f"member_{body.status}", f"user:{u.id}")
    return ok({"id": u.id, "status": u.status}, request)
