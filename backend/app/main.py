"""FastAPI 入口：/api/v1。"""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import admin, auth, line, me, positions, signals
from app.api.deps import ApiError, api_error_handler
from app.db.session import init_db

logging.basicConfig(level=logging.INFO)

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


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


for r in (auth.router, auth.admin_router, me.router, signals.router, positions.router, line.router, admin.router):
    app.include_router(r, prefix="/api/v1")


@app.get("/api/health")
def health():
    return {"ok": True}
