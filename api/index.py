"""Vercel Python 函式入口：把所有 /api/* 交給 FastAPI（建表與基本資料由 app 的 lifespan 處理）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.main import app  # noqa: E402,F401
