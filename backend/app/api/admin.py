"""管理中心：作業狀態、手動執行、重試、稽核。管理員 MFA 為下一階段。"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as M
from app.api.deps import ApiError, admin_user, audit, get_db, ok
from app.notify.dispatcher import dispatch_pending
from app.providers import get_provider
from app.workers.pipeline import run_market
from app.workers.runner import line_client

router = APIRouter(prefix="/admin", tags=["admin"])


def job_dict(j: M.Job) -> dict:
    return {"id": j.id, "job_key": j.job_key, "type": j.job_type, "market": j.market_id, "status": j.status,
            "progress": j.progress, "detail": j.detail, "error": j.error, "created_at": j.created_at.isoformat(),
            "heartbeat_at": j.heartbeat_at.isoformat()}


@router.get("/jobs")
def jobs(request: Request, admin: M.User = Depends(admin_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(M.Job).order_by(M.Job.created_at.desc()).limit(100)).all()
    watched = select(M.WatchlistItem.instrument_id)  # 只列有人監測的股票；沒人監測的不會抓資料，不算問題
    missing = db.scalars(select(M.Instrument).where(M.Instrument.state.in_(["quality_failed", "backfilling", "unavailable"]),
                                                    M.Instrument.delisted_on.is_(None),
                                                    M.Instrument.id.in_(watched))).all()
    markets = [{"id": m.id, "data_cutoff": str(m.data_cutoff) if m.data_cutoff else None, "state": m.data_state}
               for m in db.scalars(select(M.Market))]
    return ok({"jobs": [job_dict(j) for j in rows], "markets": markets,
               "problem_instruments": [{"id": i.id, "market": i.market_id, "symbol": i.symbol, "name": i.name, "state": i.state} for i in missing]},
              request)


class RunIn(BaseModel):
    market: str
    as_of: Optional[date] = None


@router.post("/jobs/run")
def run(body: RunIn, request: Request, admin: M.User = Depends(admin_user), db: Session = Depends(get_db)):
    if body.market not in ("TW", "US", "JP"):
        raise ApiError(422, "invalid", "市場需為 TW、US 或 JP。")
    d = body.as_of or date.today()
    existing = db.scalar(select(M.Job).where(M.Job.job_key.like(f"{body.market}|{d}|after_close|%")).order_by(M.Job.created_at.desc()))
    version = "1" if existing is None else f"manual-{len(db.scalars(select(M.Job).where(M.Job.job_key.like(f'{body.market}|{d}|%'))).all()) + 1}"
    job = run_market(db, get_provider(body.market), body.market, d, version)
    db.flush()
    stats = dispatch_pending(db, line_client())  # 重跑不繞過通知去重：佇列唯一鍵保證
    audit(db, admin, "job_run", f"job:{job.job_key}", {"dispatch": stats})
    return ok({"job": job_dict(job), "dispatch": stats}, request)


@router.get("/audit")
def audit_logs(request: Request, admin: M.User = Depends(admin_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(M.AuditLog).order_by(M.AuditLog.at.desc()).limit(200)).all()
    return ok([{"id": a.id, "actor_id": a.actor_id, "action": a.action, "resource": a.resource, "summary": a.summary,
                "at": a.at.isoformat()} for a in rows], request)
