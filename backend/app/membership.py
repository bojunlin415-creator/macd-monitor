"""會員方案與權限額度。

方案設定集中在 PLANS；之後開放付費只要把對應方案的 purchasable 打開並接上金流。
註冊方式由環境變數 SIGNUP_MODE 決定：
- open：任何人可用 Email 或 Google 自行註冊，立即使用免費方案（預設）
- approval：可自行申請，但需管理員核准後才能登入
- invite：只有管理員邀請的人能加入（舊行為）
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.db import models as M

SIGNUP_MODES = ("open", "approval", "invite")


def signup_mode() -> str:
    mode = os.getenv("SIGNUP_MODE", "open").strip().lower()
    return mode if mode in SIGNUP_MODES else "open"


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    watch_limit: int
    markets: tuple[str, ...]
    # LINE 推播合併門檻上限：同一批次達此數量即合併成一則（3～10，越小越省訊息量）
    line_merge_max: int
    price_twd_month: Optional[int] = None
    purchasable: bool = False  # 開放線上付款後才設為 True
    features: tuple[str, ...] = field(default_factory=tuple)


PLANS: dict[str, Plan] = {
    "free": Plan(
        id="free", name="免費會員", watch_limit=50, markets=("TW", "US", "JP"), line_merge_max=3,
        features=("自選股最多 50 檔", "台股、美股、日股週線訊號", "LINE 推播（同批次 3 則以上合併為一則）"),
    ),
    "pro": Plan(
        id="pro", name="付費會員", watch_limit=200, markets=("TW", "US", "JP"), line_merge_max=10,
        features=("自選股最多 200 檔", "台股、美股、日股週線訊號", "LINE 推播可自訂合併門檻"),
    ),
}
DEFAULT_PLAN = "free"
ADMIN_PLAN = "pro"  # 管理員一律享有最高額度


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def plan_id_of(db: Session, user: M.User) -> str:
    if user.role == "admin":
        return ADMIN_PLAN
    m = db.get(M.Membership, user.id)
    if m is None or m.plan not in PLANS:
        return DEFAULT_PLAN
    if m.current_period_end and _aware(m.current_period_end) < datetime.now(timezone.utc):
        return DEFAULT_PLAN  # 付費期間已過，回到免費方案
    return m.plan


def plan_of(db: Session, user: M.User) -> Plan:
    return PLANS[plan_id_of(db, user)]


def plan_dict(p: Plan) -> dict:
    d = asdict(p)
    d["markets"], d["features"] = list(p.markets), list(p.features)
    return d
