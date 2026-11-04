"""待送佇列投遞（規格 10.4）。在事件交易之外執行。

狀態：pending（等待發送）→ sending（發送中）→ accepted（LINE 已接收）／retry（稍後重試）／failed（發送失敗）
重試間隔 1、5、15、60 分鐘，之後每 60 分鐘，最長 24 小時；429 依 Retry-After；永久 4xx 進死信。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import models as M
from app.engine.signals import PRIORITY
from app.notify.crypto import decrypt
from app.notify.line_messages import ConditionLine, NotificationItem, plan_batch

RETRY_MINUTES = [1, 5, 15, 60]
MAX_AGE = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def item_from_payload(p: dict, kind: str, as_of: Optional[date]) -> NotificationItem:
    base = settings.app_base_url.rstrip("/")
    return NotificationItem(
        market=p["market"], symbol=p["symbol"], name=p["name"], code=p["code"], kind=kind,
        status=p["status"], close=p.get("close"), week_change=p.get("week_change"),
        h_prev=p.get("h_prev"), h=p.get("h"), rvol=p.get("rvol"), dif=p.get("dif"), dea=p.get("dea"),
        params=tuple(p.get("params", (12, 26, 9))), elapsed=p.get("elapsed", 0), expected=p.get("expected", 0),
        week_no=p.get("week_no", 0), as_of=as_of,
        conditions=[ConditionLine(c["key"], c["state"]) for c in p.get("conditions", [])],
        detail_url=f"{base}/signals/{p['event_id']}", chart_url=f"{base}/instruments/{p['instrument_id']}",
    )


def dispatch_pending(db: Session, client, now: Optional[datetime] = None, user_id: Optional[str] = None) -> dict:
    now = now or _now()
    q = select(M.NotificationOutbox).where(M.NotificationOutbox.status.in_(["pending", "retry"]))
    if user_id:  # 只送某位使用者的佇列（綁定完成、測試訊息時立即送出）
        q = q.where(M.NotificationOutbox.user_id == user_id)
    rows = db.scalars(q.order_by(M.NotificationOutbox.created_at)).all()
    rows = [r for r in rows if _aware(r.next_attempt_at) <= now]
    by_user: dict[str, list[M.NotificationOutbox]] = {}
    for r in rows:
        by_user.setdefault(r.user_id, []).append(r)
    stats = {"messages": 0, "accepted": 0, "retry": 0, "failed": 0}
    for user_id, urows in by_user.items():
        binding = db.get(M.LineBinding, user_id)
        user = db.get(M.User, user_id)
        if binding is None or binding.status != "active" or user.status != "active":
            for r in urows:  # 解綁或停用：不再推播
                r.status = "failed"
            stats["failed"] += len(urows)
            continue
        to = decrypt(binding.line_user_id_enc)
        prefs = db.get(M.NotificationPrefs, user_id)
        threshold = prefs.merge_threshold if prefs else 5
        # 同股同批次同種類的多個代碼併成一張卡：主標籤＋「也符合」
        groups: dict[tuple, list[M.NotificationOutbox]] = {}
        for r in urows:
            if r.kind in ("summary", "test"):
                groups[(r.id,)] = [r]
            else:
                groups.setdefault((r.payload_snapshot["instrument_id"], r.batch_id, r.kind), []).append(r)
        items: list[NotificationItem] = []
        item_rows: list[list[M.NotificationOutbox]] = []
        raw_msgs: list[tuple[list[M.NotificationOutbox], dict]] = []
        for g in groups.values():
            if g[0].kind in ("summary", "test"):
                raw_msgs.append((g, {"type": "text", "text": g[0].payload_snapshot["text"]}))
                continue
            g.sort(key=lambda r: PRIORITY.index(r.payload_snapshot["code"]))
            mk = db.get(M.Market, g[0].market_id)
            it = item_from_payload(g[0].payload_snapshot, g[0].kind, mk.data_cutoff if mk else None)
            it.also_codes = [r.payload_snapshot["code"] for r in g[1:]]
            items.append(it)
            item_rows.append(g)
        plan = plan_batch(items, threshold, f"{settings.app_base_url.rstrip('/')}/signals")
        sends = [([r for i in m.item_indexes for r in item_rows[i]], m.flex) for m in plan] + raw_msgs
        for orows, msg in sends:
            stats["messages"] += 1
            msg_id = str(uuid.uuid4())
            for r in orows:
                r.status = "sending"
                r.attempts += 1
            db.flush()
            res = client.push(to, [msg], orows[0].retry_key)
            for r in orows:
                db.add(M.DeliveryAttempt(outbox_id=r.id, attempt_no=r.attempts, request_id=res.request_id,
                                         http_status=res.http_status, error_class=res.error_class))
                r.sent_message_id = msg_id
                if res.ok:
                    r.status = "accepted"
                elif res.http_status and 400 <= res.http_status < 500 and res.http_status != 429:
                    r.status = "failed"  # 永久錯誤進死信
                else:
                    age = now - _aware(r.created_at)
                    if age >= MAX_AGE:
                        r.status = "failed"
                    else:
                        idx = min(r.attempts - 1, len(RETRY_MINUTES) - 1)
                        wait = timedelta(seconds=res.retry_after) if res.retry_after else timedelta(minutes=RETRY_MINUTES[idx])
                        r.status = "retry"
                        r.next_attempt_at = now + wait
            key = "accepted" if res.ok else ("failed" if orows[0].status == "failed" else "retry")
            stats[key] += 1
    return stats
