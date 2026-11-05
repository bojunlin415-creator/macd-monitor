"""LINE 綁定、Webhook、測試訊息與投遞紀錄（規格 10.1、10.4）。

綁定流程：登入後產生一次性綁定碼（10 分鐘）→ 使用者把「綁定 XXXXXX」傳給官方帳號
→ Webhook 驗簽後取得 LINE 驗證過的 userId 並關聯。不接受手動輸入 LINE ID。
"""
from __future__ import annotations

import json
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import models as M
from app.api.deps import ApiError, _aware, audit, current_user, get_db, h, ok
from app.notify.crypto import blind_index, encrypt
from app.notify.line_client import verify_signature
from app.i18n import zh_tw as T

router = APIRouter(tags=["line"])
NONCE_TTL = timedelta(minutes=10)
_SEEN_WEBHOOK_EVENTS: dict[str, float] = {}  # 依事件 ID 防重播（正式環境改用 Redis 或資料表）


def _send_now(db: Session, user_id: str) -> None:
    """先提交，再立即送出這位使用者的佇列；失敗時留在佇列，由排程重試。"""
    from app.notify.dispatcher import dispatch_pending
    from app.workers.runner import line_client
    db.commit()
    try:
        dispatch_pending(db, line_client(), user_id=user_id)
        db.commit()
    except Exception:
        db.rollback()


def _code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


@router.post("/line/link")
def start_link(request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    code = _code()
    b = db.get(M.LineBinding, user.id)
    if b is None:
        b = M.LineBinding(user_id=user.id, status="pending")
        db.add(b)
    if b.status == "active":
        raise ApiError(409, "already_linked", "已綁定 LINE，如需更換請先解除綁定。")
    b.status = "pending"
    b.link_nonce_hash = h(code)
    b.nonce_expires_at = datetime.now(timezone.utc) + NONCE_TTL
    text = f"綁定 {code}"
    bot = settings.line_bot_basic_id
    url = f"https://line.me/R/oaMessage/{urllib.parse.quote(bot)}/?{urllib.parse.quote(text)}" if bot else None
    return ok({"code": code, "message_text": text, "open_url": url, "expires_in": int(NONCE_TTL.total_seconds()),
               "configured": bool(bot)}, request)


@router.get("/line/status")
def link_status(request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    b = db.get(M.LineBinding, user.id)
    return ok({"status": b.status if b else "none"}, request)


@router.delete("/line/link")
def unlink(request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    b = db.get(M.LineBinding, user.id)
    if b:
        b.status = "unlinked"
        b.line_user_id_enc = None
        b.line_user_hash = None
        # 解綁立即停止後續推播
        for r in db.scalars(select(M.NotificationOutbox).where(M.NotificationOutbox.user_id == user.id,
                                                               M.NotificationOutbox.status.in_(["pending", "retry"]))):
            r.status = "failed"
        audit(db, user, "line_unlink", f"user:{user.id}")
    return ok({"status": "unlinked", "message": "已解除 LINE 綁定，之後不會再收到推播。"}, request)


def bind_from_webhook(db: Session, line_user_id: str, text: str) -> str | None:
    """處理「綁定 CODE」訊息；回傳要回覆的文字。"""
    parts = text.strip().split()
    if len(parts) != 2 or parts[0] != "綁定":
        return None
    b = db.scalar(select(M.LineBinding).where(M.LineBinding.link_nonce_hash == h(parts[1].upper())))
    now = datetime.now(timezone.utc)
    if b is None or b.status != "pending" or not b.nonce_expires_at or _aware(b.nonce_expires_at) < now:
        return "綁定碼無效或已過期，請回到網站重新產生。"
    idx = blind_index(line_user_id)
    other = db.scalar(select(M.LineBinding).where(M.LineBinding.line_user_hash == idx, M.LineBinding.user_id != b.user_id))
    if other is not None and other.status == "active":
        return "這個 LINE 帳號已綁定其他帳戶，請先在原帳戶解除綁定。"
    if other is not None:
        other.line_user_hash = None
    b.line_user_id_enc = encrypt(line_user_id)
    b.line_user_hash = idx
    b.status = "active"
    b.link_nonce_hash = None
    b.linked_at = now
    audit(db, None, "line_link", f"user:{b.user_id}")
    return "LINE 綁定完成！之後的訊號會即時送到這裡。\n" + T.RISK_NOTE


@router.post("/webhooks/line")
async def webhook(request: Request, x_line_signature: str = Header("", alias="X-Line-Signature"),
                  db: Session = Depends(get_db)):
    raw = await request.body()  # 以原始 body 驗簽
    if not settings.line_channel_secret or not verify_signature(settings.line_channel_secret, raw, x_line_signature):
        raise ApiError(401, "bad_signature", "簽章驗證失敗。")
    payload = json.loads(raw)
    for ev in payload.get("events", []):
        eid = ev.get("webhookEventId")
        if eid in _SEEN_WEBHOOK_EVENTS:
            continue
        _SEEN_WEBHOOK_EVENTS[eid] = datetime.now().timestamp()
        src = ev.get("source", {})
        if ev.get("type") == "message" and ev.get("message", {}).get("type") == "text" and src.get("type") == "user":
            text = bind_from_webhook(db, src["userId"], ev["message"]["text"])
            if text and ev.get("replyToken"):
                db.commit()  # 綁定先寫入，再回覆
                from app.workers.runner import line_client
                line_client().reply(ev["replyToken"], text)
        elif ev.get("type") == "unfollow" and src.get("userId"):
            b = db.scalar(select(M.LineBinding).where(M.LineBinding.line_user_hash == blind_index(src["userId"])))
            if b:
                b.status = "unlinked"
    return ok({"received": True}, request)


@router.post("/line/test")
def send_test(request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    b = db.get(M.LineBinding, user.id)
    if b is None or b.status != "active":
        raise ApiError(409, "not_linked", "尚未綁定 LINE。")
    now = datetime.now(timezone.utc)
    db.add(M.NotificationOutbox(user_id=user.id, kind="test", dedupe_key=f"test|{user.id}|{now.isoformat()}",
                                payload_snapshot={"text": "這是一則測試訊息，LINE 通知運作正常。\n" + T.RISK_NOTE}))
    _send_now(db, user.id)
    return ok({"message": "測試訊息已送出，請查看 LINE。"}, request)


@router.get("/line/deliveries")
def deliveries(request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(M.NotificationOutbox).where(M.NotificationOutbox.user_id == user.id)
                      .order_by(M.NotificationOutbox.created_at.desc()).limit(30)).all()
    out = []
    for r in rows:
        p = r.payload_snapshot or {}
        out.append({"id": r.id, "kind": r.kind, "status": r.status, "attempts": r.attempts,
                    "created_at": r.created_at.isoformat(), "symbol": p.get("symbol"), "name": p.get("name"),
                    "market": p.get("market"), "code": p.get("code")})
    return ok(out, request)
