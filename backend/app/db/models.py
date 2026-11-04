"""資料模型（規格第 7 章）。欄位名維持英文，介面文字由對照表產生。

SQLAlchemy 2.x，正式環境 PostgreSQL；開發與測試可用 SQLite。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


PRICE = Numeric(24, 10, asdecimal=False)
VOLUME = Numeric(28, 8, asdecimal=False)


class Base(DeclarativeBase):
    pass


# ---- 公共行情 -------------------------------------------------------------
class Market(Base):
    __tablename__ = "markets"
    id: Mapped[str] = mapped_column(String(4), primary_key=True)  # TW / US / JP
    mic: Mapped[str] = mapped_column(String(8))
    timezone: Mapped[str] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(String(3))
    close_time: Mapped[str] = mapped_column(String(5))  # 當地收盤 HH:MM（實際以日曆為準）
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    data_cutoff: Mapped[Optional[date]] = mapped_column(Date)
    data_state: Mapped[str] = mapped_column(String(12), default="ok")  # ok / stale / error


class MarketSession(Base):
    __tablename__ = "market_sessions"
    market_id: Mapped[str] = mapped_column(ForeignKey("markets.id"), primary_key=True)
    local_date: Mapped[date] = mapped_column(Date, primary_key=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)  # 提早收盤以分鐘比例折算
    calendar_version: Mapped[str] = mapped_column(String(20), default="1")


class Instrument(Base):
    __tablename__ = "instruments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    market_id: Mapped[str] = mapped_column(ForeignKey("markets.id"))
    mic: Mapped[str] = mapped_column(String(8))
    symbol: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3))
    lot_size: Mapped[int] = mapped_column(Integer, default=1)  # 台股一張 1000 股
    listed_on: Mapped[Optional[date]] = mapped_column(Date)
    delisted_on: Mapped[Optional[date]] = mapped_column(Date)
    license_status: Mapped[str] = mapped_column(String(16), default="sample")
    state: Mapped[str] = mapped_column(String(16), default="backfilling")  # backfilling/warmup/ready/quality_failed
    weeks_available: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("mic", "symbol", name="uq_instrument_mic_symbol"),)


class DailyBarRow(Base):
    __tablename__ = "daily_bars"
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String(20), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    open: Mapped[float] = mapped_column(PRICE)
    high: Mapped[float] = mapped_column(PRICE)
    low: Mapped[float] = mapped_column(PRICE)
    close: Mapped[float] = mapped_column(PRICE)
    volume: Mapped[float] = mapped_column(VOLUME)
    is_final: Mapped[bool] = mapped_column(Boolean, default=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CorporateActionRow(Base):
    __tablename__ = "corporate_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), index=True)
    effective_date: Mapped[date] = mapped_column(Date)
    type: Mapped[str] = mapped_column(String(16))
    price_factor: Mapped[float] = mapped_column(Float)
    volume_factor: Mapped[float] = mapped_column(Float)
    basis: Mapped[str] = mapped_column(String(16), default="backward")
    revision: Mapped[int] = mapped_column(Integer, default=1)


class IndicatorSnapshot(Base):
    """每次判斷時的輸入與結果，供訊號詳情與稽核重現。"""

    __tablename__ = "indicator_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), index=True)
    week_key: Mapped[date] = mapped_column(Date)
    param_hash: Mapped[str] = mapped_column(String(32))
    input_hash: Mapped[str] = mapped_column(String(64))
    engine_version: Mapped[str] = mapped_column(String(16))
    data: Mapped[dict] = mapped_column(JSON)  # DIF/DEA/H/MA/RVOL/close/eps/時段…（本週與前週）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---- 使用者與權限 -----------------------------------------------------------
class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(80), default="")
    role: Mapped[str] = mapped_column(String(10), default="member")  # member / admin
    status: Mapped[str] = mapped_column(String(10), default="active")  # active / disabled
    locale: Mapped[str] = mapped_column(String(10), default="zh-TW")
    timezone: Mapped[str] = mapped_column(String(40), default="Asia/Taipei")
    theme: Mapped[str] = mapped_column(String(8), default="system")  # system / light / dark
    updown_color: Mapped[str] = mapped_column(String(12), default="red_up")  # red_up / green_up
    table_density: Mapped[str] = mapped_column(String(10), default="normal")
    consent_version: Mapped[Optional[str]] = mapped_column(String(10))
    onboarding_done: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_state: Mapped[str] = mapped_column(String(10), default="none")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(255))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    inviter_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class LoginToken(Base):
    """Email 一次性登入連結：15 分鐘到期、用後即失效。"""

    __tablename__ = "login_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class WebSession(Base):
    __tablename__ = "web_sessions"
    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    csrf_token: Mapped[str] = mapped_column(String(64))


# ---- 個人資料 -------------------------------------------------------------
class WatchGroup(Base):
    __tablename__ = "watch_groups"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    group_id: Mapped[Optional[str]] = mapped_column(ForeignKey("watch_groups.id"))
    baseline_week: Mapped[Optional[date]] = mapped_column(Date)  # 加入當週只建立狀態，不通知
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    instrument: Mapped[Instrument] = relationship()
    __table_args__ = (UniqueConstraint("user_id", "instrument_id", name="uq_watch_user_instrument"),)


class WatchQuota(Base):
    """配額列：新增自選時鎖定此列，同交易檢查 ≤ 200。"""

    __tablename__ = "watch_quotas"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    used: Mapped[int] = mapped_column(Integer, default=0)


class RuleVersion(Base):
    __tablename__ = "rule_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    instrument_id: Mapped[Optional[str]] = mapped_column(ForeignKey("instruments.id"))  # 空值為全域
    fast: Mapped[int] = mapped_column(Integer, default=12)
    slow: Mapped[int] = mapped_column(Integer, default=26)
    signal: Mapped[int] = mapped_column(Integer, default=9)
    rvol_up: Mapped[float] = mapped_column(Float, default=1.20)
    effective_week: Mapped[date] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SignalEvent(Base):
    __tablename__ = "signal_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    week_key: Mapped[date] = mapped_column(Date)
    code: Mapped[str] = mapped_column(String(10))
    level: Mapped[str] = mapped_column(String(10))
    direction: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(24))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    rule_version_id: Mapped[Optional[str]] = mapped_column(ForeignKey("rule_versions.id"))
    snapshot_id: Mapped[Optional[str]] = mapped_column(ForeignKey("indicator_snapshots.id"))
    reason_json: Mapped[dict] = mapped_column(JSON, default=dict)
    data_revision: Mapped[int] = mapped_column(Integer, default=0)
    seen: Mapped[bool] = mapped_column(Boolean, default=False)
    first_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    instrument: Mapped[Instrument] = relationship()
    snapshot: Mapped[Optional[IndicatorSnapshot]] = relationship()
    __table_args__ = (
        UniqueConstraint("user_id", "instrument_id", "week_key", "code", name="uq_event_week_code"),
        Index("ix_event_user_week", "user_id", "week_key"),
    )


class SignalRevision(Base):
    __tablename__ = "signal_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    event_id: Mapped[str] = mapped_column(ForeignKey("signal_events.id"), index=True)
    from_status: Mapped[Optional[str]] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24))
    input_hash: Mapped[str] = mapped_column(String(64))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    correction_reason: Mapped[Optional[str]] = mapped_column(Text)


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"))
    type: Mapped[str] = mapped_column(String(10))  # opening/buy/sell/split/correction
    date: Mapped[date] = mapped_column(Date)
    qty: Mapped[float] = mapped_column(VOLUME, default=0)
    price: Mapped[float] = mapped_column(PRICE, default=0)
    fee: Mapped[float] = mapped_column(PRICE, default=0)
    split_ratio: Mapped[float] = mapped_column(Float, default=1.0)
    currency: Mapped[str] = mapped_column(String(3))
    note: Mapped[str] = mapped_column(String(200), default="")
    voided_by: Mapped[Optional[str]] = mapped_column(String(36))  # 更正：沖銷紀錄
    idempotency_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    instrument: Mapped[Instrument] = relationship()
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_tx_idem"),)


# ---- 通知 ---------------------------------------------------------------
class LineBinding(Base):
    __tablename__ = "line_bindings"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    line_user_id_enc: Mapped[Optional[str]] = mapped_column(String(255))
    line_user_hash: Mapped[Optional[str]] = mapped_column(String(64), unique=True)  # 不可跨帳戶重複綁定
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending/active/unlinked
    link_nonce_hash: Mapped[Optional[str]] = mapped_column(String(64))
    nonce_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    linked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class NotificationPrefs(Base):
    __tablename__ = "notification_prefs"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    # 依等級訂閱：可關閉的代碼；NEG_WEAK / POS_WEAK 依 P-V06 不可關閉
    muted_codes: Mapped[list] = mapped_column(JSON, default=list)
    merge_threshold: Mapped[int] = mapped_column(Integer, default=5)
    weekly_summary: Mapped[bool] = mapped_column(Boolean, default=True)
    paused_until: Mapped[Optional[date]] = mapped_column(Date)


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    event_id: Mapped[Optional[str]] = mapped_column(ForeignKey("signal_events.id"))
    kind: Mapped[str] = mapped_column(String(12))  # event/confirm/correction/summary/test
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True)
    payload_snapshot: Mapped[dict] = mapped_column(JSON)
    retry_key: Mapped[str] = mapped_column(String(36), default=uid)  # X-Line-Retry-Key
    status: Mapped[str] = mapped_column(String(10), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    batch_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    market_id: Mapped[Optional[str]] = mapped_column(String(4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sent_message_id: Mapped[Optional[str]] = mapped_column(String(36))  # 合併訊息共享


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    outbox_id: Mapped[str] = mapped_column(ForeignKey("notification_outbox.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[Optional[str]] = mapped_column(String(64))
    http_status: Mapped[Optional[int]] = mapped_column(Integer)
    error_class: Mapped[Optional[str]] = mapped_column(String(40))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---- 作業與稽核 -------------------------------------------------------------
class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_key: Mapped[str] = mapped_column(String(120), unique=True)  # market|date|type|version
    job_type: Mapped[str] = mapped_column(String(20))
    market_id: Mapped[Optional[str]] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(10), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    actor_id: Mapped[Optional[str]] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(40))
    resource: Mapped[str] = mapped_column(String(80))
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(36))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
