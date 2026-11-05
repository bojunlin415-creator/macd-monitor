"""寄信轉接層。設定 RESEND_API_KEY 時以 Resend 寄出；否則只寫日誌（開發環境）。"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from app.config import settings

log = logging.getLogger(__name__)


def email_configured() -> bool:
    return bool(settings.resend_api_key)


def send_email(to: str, subject: str, text: str) -> bool:
    if not settings.resend_api_key:
        log.info("［寄信乾跑］%s｜%s", to.split("@")[-1], subject)
        return False
    body = json.dumps({"from": settings.email_from, "to": [to], "subject": subject, "text": text}).encode()
    req = urllib.request.Request("https://api.resend.com/emails", data=body, method="POST", headers={
        "Authorization": f"Bearer {settings.resend_api_key}", "Content-Type": "application/json",
        "User-Agent": "macd-monitor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, TimeoutError) as e:
        log.warning("寄信失敗：%s", type(e).__name__)
        return False
