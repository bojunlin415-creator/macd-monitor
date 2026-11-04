"""LINE 使用者 ID 加密保存（Fernet）。金鑰由 SECRET_KEY 衍生；正式環境應改由 KMS／秘密管理服務提供。"""
from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet

from app.config import settings


def _fernet() -> Fernet:
    key = hashlib.sha256(("line-id|" + settings.secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def blind_index(plain: str) -> str:
    """用於唯一約束：同一 LINE 帳號不可跨帳戶重複綁定。"""
    return hmac.new(settings.secret_key.encode(), ("line|" + plain).encode(), hashlib.sha256).hexdigest()
