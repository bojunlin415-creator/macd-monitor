"""LINE Messaging API 轉接層。Channel token 只從環境變數／秘密管理服務讀取。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

PUSH_URL = "https://api.line.me/v2/bot/message/push"
REPLY_URL = "https://api.line.me/v2/bot/message/reply"


@dataclass
class PushResult:
    ok: bool
    http_status: Optional[int]
    request_id: Optional[str] = None
    retry_after: Optional[int] = None
    error_class: Optional[str] = None


class LineClient:
    def __init__(self, token: str):
        self.token = token

    def push(self, to: str, messages: list[dict], retry_key: str) -> PushResult:
        body = json.dumps({"to": to, "messages": messages[:5]}).encode()
        req = urllib.request.Request(PUSH_URL, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
            "X-Line-Retry-Key": retry_key,  # 逾時重試沿用同一把鍵
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return PushResult(True, r.status, r.headers.get("x-line-request-id"))
        except urllib.error.HTTPError as e:
            # 409 代表同一 retry key 已被接受過：視為成功
            if e.code == 409:
                return PushResult(True, 409, e.headers.get("x-line-request-id"))
            ra = e.headers.get("Retry-After")
            return PushResult(False, e.code, e.headers.get("x-line-request-id"),
                              int(ra) if ra and ra.isdigit() else None, f"http_{e.code}")
        except (urllib.error.URLError, TimeoutError) as e:
            return PushResult(False, None, error_class=type(e).__name__)


    def reply(self, reply_token: str, text: str) -> bool:
        """以回覆權杖回覆使用者訊息（不計入每月推播額度）。"""
        body = json.dumps({"replyToken": reply_token, "messages": [{"type": "text", "text": text}]}).encode()
        req = urllib.request.Request(REPLY_URL, data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return 200 <= r.status < 300
        except (urllib.error.URLError, TimeoutError):
            log.warning("LINE 回覆失敗")
            return False


class DryRunClient:
    """開發／測試環境：不真的送出，只記錄。測試環境禁止對正式成員推播。"""

    def __init__(self):
        self.sent: list[tuple[str, list[dict], str]] = []

    def push(self, to: str, messages: list[dict], retry_key: str) -> PushResult:
        self.sent.append((to, messages, retry_key))
        log.info("［乾跑］LINE 推播給 %s：%s", to[:6] + "…", messages[0].get("altText") or messages[0].get("text"))
        return PushResult(True, 200, "dry-run")

    def reply(self, reply_token: str, text: str) -> bool:
        self.sent.append(("reply:" + reply_token, [{"type": "text", "text": text}], ""))
        log.info("［乾跑］LINE 回覆：%s", text)
        return True


def verify_signature(channel_secret: str, raw_body: bytes, signature: str) -> bool:
    """Webhook 以原始 request body 驗證簽章。"""
    mac = hmac.new(channel_secret.encode(), raw_body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(), signature or "")
