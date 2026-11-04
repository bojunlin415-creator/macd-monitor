"""設定：全部由環境變數提供；密鑰不得寫入程式碼或版本庫。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./macd_dev.db"))
    app_base_url: str = field(default_factory=lambda: os.getenv("APP_BASE_URL", "http://localhost:5173"))
    env: str = field(default_factory=lambda: os.getenv("APP_ENV", "dev"))  # dev / test / prod
    secret_key: str = field(default_factory=lambda: os.getenv("SECRET_KEY", "dev-only-secret-change-me"))
    line_channel_token: str = field(default_factory=lambda: os.getenv("LINE_CHANNEL_ACCESS_TOKEN", ""))
    line_channel_secret: str = field(default_factory=lambda: os.getenv("LINE_CHANNEL_SECRET", ""))
    line_bot_basic_id: str = field(default_factory=lambda: os.getenv("LINE_BOT_BASIC_ID", ""))
    data_provider: str = field(default_factory=lambda: os.getenv("DATA_PROVIDER", "sample"))
    # 測試環境禁止對正式成員推播：非 prod 一律走乾跑，除非明確開啟
    line_live: bool = field(default_factory=lambda: os.getenv("LINE_LIVE", "0") == "1")

    @property
    def is_dev(self) -> bool:
        return self.env != "prod"


settings = Settings()
