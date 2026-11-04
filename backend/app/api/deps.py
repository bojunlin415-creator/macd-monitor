"""API 共用：資料庫、身分驗證、統一回應格式與錯誤。"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator, Optional

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import models as M
from app.db.session import SessionLocal

SESSION_COOKIE = "sid"
MEMBER_IDLE = timedelta(minutes=60)
ADMIN_IDLE = timedelta(minutes=15)
MAX_AGE = timedelta(days=7)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, field_errors: Optional[dict] = None, retryable: bool = False):
        self.status, self.code, self.message = status, code, message
        self.field_errors, self.retryable = field_errors or {}, retryable


def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={
        "data": None,
        "meta": {"request_id": getattr(request.state, "request_id", None)},
        "error": {"code": exc.code, "message": exc.message, "field_errors": exc.field_errors, "retryable": exc.retryable},
    })


def ok(data: Any, request: Optional[Request] = None, **meta) -> dict:
    return {"data": data, "meta": {"request_id": getattr(getattr(request, "state", None), "request_id", None) if request else None,
                                   "as_of": datetime.now(timezone.utc).isoformat(), **meta}, "error": None}


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def create_session(db: Session, user: M.User) -> tuple[str, str]:
    sid = new_token()
    csrf = secrets.token_urlsafe(24)
    db.add(M.WebSession(id_hash=h(sid), user_id=user.id, csrf_token=csrf))
    return sid, csrf


def current_user(request: Request, db: Session = Depends(get_db)) -> M.User:
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        raise ApiError(401, "unauthenticated", "請先登入。")
    sess = db.get(M.WebSession, h(sid))
    if sess is None:
        raise ApiError(401, "unauthenticated", "登入已失效，請重新登入。")
    user = db.get(M.User, sess.user_id)
    now = datetime.now(timezone.utc)
    idle = ADMIN_IDLE if user.role == "admin" else MEMBER_IDLE
    if user.status != "active" or now - _aware(sess.last_seen_at) > idle or now - _aware(sess.created_at) > MAX_AGE:
        db.delete(sess)
        raise ApiError(401, "unauthenticated", "登入已逾時，請重新登入。")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("X-CSRF-Token") != sess.csrf_token:
            raise ApiError(403, "csrf", "請重新整理頁面後再試一次。")
    sess.last_seen_at = now
    request.state.session = sess
    return user


def admin_user(user: M.User = Depends(current_user)) -> M.User:
    if user.role != "admin":
        raise ApiError(404, "not_found", "找不到這個頁面，或你沒有查看權限。")
    return user


def audit(db: Session, actor: Optional[M.User], action: str, resource: str, summary: Optional[dict] = None) -> None:
    db.add(M.AuditLog(actor_id=actor.id if actor else None, action=action, resource=resource,
                      summary=summary or {}, correlation_id=str(uuid.uuid4())))


def next_week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday()) + timedelta(days=7)
