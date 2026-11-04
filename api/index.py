"""Vercel Python 函式入口：把所有 /api/* 交給 FastAPI。"""
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.main import _bootstrap, app  # noqa: E402,F401
from app.db.session import init_db  # noqa: E402

try:  # 冷啟動時建立資料表與基本資料（冪等）
    init_db()
    _bootstrap()
except Exception:  # 資料庫尚未設定時仍讓函式啟動，由各請求回報錯誤
    logging.getLogger("api").exception("初始化失敗")
