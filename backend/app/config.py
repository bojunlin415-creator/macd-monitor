"""設定：全部由環境變數提供；密鑰不得寫入程式碼或版本庫。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _db_url() -> str:
    """支援 Vercel／Neon 注入的 postgres:// 連線字串。"""
    url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or "sqlite:///./macd_dev.db"
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _base_url() -> str:
    if os.getenv("APP_BASE_URL"):
        return os.getenv("APP_BASE_URL", "")
    host = os.getenv("VERCEL_PROJECT_PRODUCTION_URL")
    return f"https://{host}" if host else "http://localhost:5173"


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _db_url())
    app_base_url: str = field(default_factory=lambda: _base_url())
    # 在 Vercel 上預設為正式環境，避免開發用登入連結外露
    env: str = field(default_factory=lambda: os.getenv("APP_ENV") or ("prod" if os.getenv("VERCEL") else "dev"))
    secret_key: str = field(default_factory=lambda: os.getenv("SECRET_KEY", "dev-only-secret-change-me"))
    line_channel_token: str = field(default_factory=lambda: os.getenv("LINE_CHANNEL_ACCESS_TOKEN", ""))
    line_channel_secret: str = field(default_factory=lambda: os.getenv("LINE_CHANNEL_SECRET", ""))
    line_bot_basic_id: str = field(default_factory=lambda: os.getenv("LINE_BOT_BASIC_ID", ""))
    data_provider: str = field(default_factory=lambda: os.getenv("DATA_PROVIDER", "sample"))
    cron_secret: str = field(default_factory=lambda: os.getenv("CRON_SECRET", ""))
    admin_email: str = field(default_factory=lambda: os.getenv("ADMIN_EMAIL", ""))
    infoway_api_key: str = field(default_factory=lambda: os.getenv("INFOWAY_API_KEY", ""))
    google_client_id: str = field(default_factory=lambda: os.getenv("GOOGLE_CLIENT_ID", ""))
    google_client_secret: str = field(default_factory=lambda: os.getenv("GOOGLE_CLIENT_SECRET", ""))
    resend_api_key: str = field(default_factory=lambda: os.getenv("RESEND_API_KEY", ""))
    email_from: str = field(default_factory=lambda: os.getenv("EMAIL_FROM", "MACD 監測系統 <onboarding@resend.dev>"))
    # 測試環境禁止對正式成員推播：非 prod 一律走乾跑，除非明確開啟
    line_live: bool = field(default_factory=lambda: os.getenv("LINE_LIVE", "0") == "1")

    @property
    def is_dev(self) -> bool:
        return self.env != "prod"


settings = Settings()
