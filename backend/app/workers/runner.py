"""工作者入口：排程各市場盤後作業、週末確認與每週摘要，並投遞待送佇列。

用法：
  python -m app.workers.runner once --market TW --date 2026-11-04   # 跑一次
  python -m app.workers.runner loop                                 # 常駐排程
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import settings
from app.db import models as M
from app.db.session import init_db, session_scope
from app.notify.dispatcher import dispatch_pending
from app.notify.line_client import DryRunClient, LineClient
from app.providers import get_provider
from app.workers.pipeline import run_market
from app.workers.summary import queue_weekly_summaries

log = logging.getLogger("worker")

MARKET_TZ = {"TW": "Asia/Taipei", "US": "America/New_York", "JP": "Asia/Tokyo"}
MARKET_CLOSE = {"TW": (13, 30), "US": (16, 0), "JP": (15, 30)}  # 實際以交易日曆為準（提早收盤另計）
FIRST_DELAY = timedelta(minutes=30)

_dry = DryRunClient()


def line_client():
    if settings.line_channel_token and (settings.line_live or not settings.is_dev):
        return LineClient(settings.line_channel_token)
    return _dry


def due_markets(now_utc: datetime) -> list[tuple[str, date]]:
    """收盤後 30 分鐘起，回傳該跑的 (市場, 當地日期)。"""
    out = []
    for m, tz in MARKET_TZ.items():
        local = now_utc.astimezone(ZoneInfo(tz))
        h, mi = MARKET_CLOSE[m]
        close = local.replace(hour=h, minute=mi, second=0, microsecond=0)
        if local.weekday() < 5 and local >= close + FIRST_DELAY:
            out.append((m, local.date()))
    return out


def tick(now_utc: datetime | None = None) -> None:
    now_utc = now_utc or datetime.now(ZoneInfo("UTC"))
    provider = get_provider()
    for market, d in due_markets(now_utc):
        with session_scope() as db:
            done = db.scalar(select(M.Job).where(M.Job.job_key == f"{market}|{d}|after_close|1", M.Job.status == "done"))
            if done:
                continue
            job = run_market(db, provider, market, d)
            log.info("市場作業 %s：%s %s", job.job_key, job.status, job.detail)
    tpe = now_utc.astimezone(ZoneInfo("Asia/Taipei"))
    if tpe.weekday() == 5 and tpe.hour >= 12:  # 週六 12:00（台北）每週摘要
        with session_scope() as db:
            queue_weekly_summaries(db, tpe.date())
    with session_scope() as db:
        stats = dispatch_pending(db, line_client())
        if stats["messages"]:
            log.info("LINE 投遞：%s", stats)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("once")
    o.add_argument("--market", required=True, choices=["TW", "US", "JP"])
    o.add_argument("--date", type=date.fromisoformat, default=date.today())
    o.add_argument("--version", default="1")
    sub.add_parser("loop")
    a = ap.parse_args()
    init_db()
    if a.cmd == "once":
        with session_scope() as db:
            job = run_market(db, get_provider(), a.market, a.date, a.version)
            print(job.job_key, job.status, job.detail, job.error or "")
        with session_scope() as db:
            print("LINE 投遞：", dispatch_pending(db, line_client()))
    else:
        while True:
            try:
                tick()
            except Exception:
                log.exception("排程執行失敗")
            time.sleep(60)


if __name__ == "__main__":
    main()
