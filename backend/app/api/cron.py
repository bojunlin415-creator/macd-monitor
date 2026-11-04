"""Vercel Cron 進入點。Vercel 會帶 Authorization: Bearer $CRON_SECRET。

各市場每日收盤後一次：判斷暫定週；週五收盤後的那次同時完成週末確認。
每次執行後立即投遞待送佇列（含先前的重試）。
"""
from __future__ import annotations

import hmac
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request

from app.config import settings
from app.api.deps import ApiError, ok
from app.db.session import session_scope
from app.notify.dispatcher import dispatch_pending
from app.providers import get_provider
from app.workers.pipeline import run_market
from app.workers.runner import MARKET_TZ, line_client
from app.workers.summary import queue_weekly_summaries

router = APIRouter(prefix="/cron", tags=["cron"])


def _check(request: Request) -> None:
    auth = request.headers.get("Authorization", "")
    if not settings.cron_secret or not hmac.compare_digest(auth, f"Bearer {settings.cron_secret}"):
        raise ApiError(404, "not_found", "找不到這個頁面，或你沒有查看權限。")


def last_trading_day(market: str, now_utc: datetime) -> date:
    d = now_utc.astimezone(ZoneInfo(MARKET_TZ[market])).date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


@router.get("/market/{market}")
def cron_market(market: str, request: Request):
    _check(request)
    if market not in MARKET_TZ:
        raise ApiError(404, "not_found", "找不到這個頁面，或你沒有查看權限。")
    d = last_trading_day(market, datetime.now(ZoneInfo("UTC")))
    with session_scope() as db:
        job = run_market(db, get_provider(), market, d, version=datetime.now(ZoneInfo("UTC")).strftime("%H%M"))
        result = {"job": job.job_key, "status": job.status, "detail": job.detail, "error": job.error}
    with session_scope() as db:
        result["dispatch"] = dispatch_pending(db, line_client())
    return ok(result, request)


@router.get("/summary")
def cron_summary(request: Request):
    _check(request)
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    with session_scope() as db:
        n = queue_weekly_summaries(db, today)
    with session_scope() as db:
        stats = dispatch_pending(db, line_client())
    return ok({"queued": n, "dispatch": stats}, request)


@router.get("/dispatch")
def cron_dispatch(request: Request):
    _check(request)
    with session_scope() as db:
        return ok(dispatch_pending(db, line_client()), request)
