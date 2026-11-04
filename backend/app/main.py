"""FastAPI 入口：/api/v1。"""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api import admin, auth, cron, google, line, me, positions, signals
from app.api.deps import ApiError, api_error_handler
from app.db.session import init_db

logging.basicConfig(level=logging.INFO)

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app):
    try:
        init_db()
        _bootstrap()
    except Exception:  # 資料庫尚未設定或暫時無法連線時仍讓服務啟動，由各請求回報 503
        logging.getLogger("api").exception("啟動時初始化資料庫失敗")
    yield


def _bootstrap() -> None:
    """建立市場與示意股票；若設定 ADMIN_EMAIL 則建立管理員（冪等）。"""
    from app.config import settings
    from app.db.session import session_scope
    from app.seed import seed
    try:
        with session_scope() as db:
            seed(db, settings.admin_email or None)
    except IntegrityError:  # 多個執行個體同時冷啟動，另一個已寫入基本資料
        logging.getLogger("api").info("基本資料已由其他執行個體建立")
    except Exception:
        logging.getLogger("api").exception("初始化資料失敗")


app = FastAPI(title="MACD 監測系統 API", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json",
              lifespan=lifespan)
app.add_exception_handler(ApiError, api_error_handler)

READ_LIMIT, WRITE_LIMIT = 120, 30  # 每人每分鐘
_hits: dict[tuple[str, bool], deque] = defaultdict(deque)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    key = (request.cookies.get("sid") or (request.client.host if request.client else "-"),
           request.method not in ("GET", "HEAD"))
    q = _hits[key]
    now = time.time()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= (WRITE_LIMIT if key[1] else READ_LIMIT) and not request.url.path.startswith("/api/v1/webhooks"):
        return JSONResponse(status_code=429, content={"data": None, "meta": {"request_id": request.state.request_id},
                            "error": {"code": "rate_limited", "message": "操作太頻繁，請稍候再試。", "retryable": True}})
    q.append(now)
    resp = await call_next(request)
    resp.headers["X-Request-Id"] = request.state.request_id
    return resp


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    fields = {".".join(str(p) for p in e["loc"][1:]): "欄位格式不正確。" for e in exc.errors()}
    return JSONResponse(status_code=400, content={"data": None, "meta": {"request_id": request.state.request_id},
                        "error": {"code": "bad_request", "message": "輸入格式不正確，請檢查標示的欄位。",
                                  "field_errors": fields, "retryable": False}})


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    logging.getLogger("api").exception("未處理錯誤")
    return JSONResponse(status_code=500, content={"data": None, "meta": {"request_id": getattr(request.state, "request_id", None)},
                        "error": {"code": "internal", "message": "系統發生錯誤，請稍後再試。", "retryable": True}})


for r in (auth.router, auth.admin_router, google.router, me.router, signals.router, positions.router, line.router, admin.router, cron.router):
    app.include_router(r, prefix="/api/v1")


@app.get("/api/health")
def health():
    from sqlalchemy import text
    from app.db.session import engine
    try:
        with engine.connect() as c:
            c.execute(text("select 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"ok": db_ok, "database": "ok" if db_ok else "unavailable"}


@app.exception_handler(OperationalError)
async def db_unavailable(request: Request, exc: OperationalError):
    logging.getLogger("api").error("資料庫無法連線：%s", exc.__class__.__name__)
    return JSONResponse(status_code=503, content={"data": None, "meta": {"request_id": getattr(request.state, "request_id", None)},
                        "error": {"code": "db_unavailable", "message": "資料庫暫時無法連線，請稍後再試。", "retryable": True}})
