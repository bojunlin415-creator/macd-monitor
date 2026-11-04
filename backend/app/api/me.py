"""個人資料：自選、匯入、規則、偏好、引導。所有查詢一律以登入者 user_id 過濾。"""
from __future__ import annotations

import csv
import io
import re
import time
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models as M
from app.membership import PLANS, plan_dict, plan_of
from app.engine.indicators import MacdParams
from app.engine.signals import MANDATORY_CODES, SIGNAL_DEFS
from app.engine.weekly import week_key
from app.api import watch_xlsx
from app.providers import get_provider
from app.api.deps import ApiError, audit, current_user, get_db, next_week_monday, ok

router = APIRouter(tags=["me"])

WATCH_LIMIT = PLANS["pro"].watch_limit  # 單次匯入／請求的最大筆數；實際上限依方案（見 app/membership.py）
CONSENT_VERSION = "1.0"


def instrument_dict(i: M.Instrument) -> dict:
    return {"id": i.id, "market": i.market_id, "mic": i.mic, "symbol": i.symbol, "name": i.name,
            "currency": i.currency, "lot_size": i.lot_size, "license_status": i.license_status,
            "state": i.state, "weeks_available": i.weeks_available}


# ---- 股票搜尋 --------------------------------------------------------------
@router.get("/instruments")
def search(request: Request, q: str = "", market: Optional[str] = None, user: M.User = Depends(current_user),
           db: Session = Depends(get_db)):
    stmt = select(M.Instrument).where(M.Instrument.delisted_on.is_(None))
    if market:
        stmt = stmt.where(M.Instrument.market_id == market)
    if q:
        like = f"%{q}%"
        stmt = stmt.where((M.Instrument.symbol.ilike(like)) | (M.Instrument.name.ilike(like)))
    rows = db.scalars(stmt.order_by(M.Instrument.market_id, M.Instrument.symbol).limit(50)).all()
    watched = {w for w in db.scalars(select(M.WatchlistItem.instrument_id).where(M.WatchlistItem.user_id == user.id))}
    return ok([{**instrument_dict(i), "watched": i.id in watched} for i in rows], request)


# ---- 自選 -----------------------------------------------------------------
def _lock_quota(db: Session, user_id: str) -> int:
    """鎖定配額列並回傳目前使用量（PostgreSQL 以 FOR UPDATE 序列化並發新增）。"""
    if db.get(M.WatchQuota, user_id) is None:
        db.add(M.WatchQuota(user_id=user_id, used=0))
        db.flush()
    db.scalar(select(M.WatchQuota).where(M.WatchQuota.user_id == user_id).with_for_update())
    return db.scalar(select(func.count()).select_from(M.WatchlistItem).where(M.WatchlistItem.user_id == user_id))


def _add_watches(db: Session, user: M.User, instrument_ids: list[str], group_id: Optional[str] = None) -> list[M.WatchlistItem]:
    plan = plan_of(db, user)
    limit = plan.watch_limit
    used = _lock_quota(db, user.id)
    existing = set(db.scalars(select(M.WatchlistItem.instrument_id).where(M.WatchlistItem.user_id == user.id)))
    new_ids = list(dict.fromkeys(i for i in instrument_ids if i not in existing))  # 重複 ID 不重複計數
    if used + len(new_ids) > limit:
        over = used + len(new_ids) - limit
        raise ApiError(409, "quota_exceeded", f"{plan.name}的自選股上限是 {limit} 檔，請再取消勾選 {over} 檔。", retryable=False)
    this_week = week_key(date.today())
    items = []
    for iid in new_ids:
        inst = db.get(M.Instrument, iid)
        if inst is None:
            raise ApiError(422, "unknown_instrument", "找不到指定的股票。")
        if inst.market_id not in plan.markets:
            raise ApiError(403, "plan_market", f"{plan.name}不包含這個市場的股票。")
        w = M.WatchlistItem(user_id=user.id, instrument_id=iid, group_id=group_id, baseline_week=this_week)
        db.add(w)
        items.append(w)
    q = db.get(M.WatchQuota, user.id)
    q.used = used + len(new_ids)
    db.flush()
    return items


def watch_dict(w: M.WatchlistItem, group_names: dict) -> dict:
    return {"id": w.id, "instrument": instrument_dict(w.instrument), "group_id": w.group_id,
            "group": group_names.get(w.group_id), "created_at": w.created_at.isoformat()}


@router.get("/watchlist")
def list_watch(request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(M.WatchlistItem).where(M.WatchlistItem.user_id == user.id).order_by(M.WatchlistItem.created_at)).all()
    groups = {g.id: g.name for g in db.scalars(select(M.WatchGroup).where(M.WatchGroup.user_id == user.id))}
    return ok({"items": [watch_dict(w, groups) for w in rows], "used": len(rows), "limit": plan_of(db, user).watch_limit,
               "groups": [{"id": k, "name": v} for k, v in groups.items()]}, request)


class WatchIn(BaseModel):
    instrument_ids: list[str] = Field(min_length=1, max_length=200)
    group: Optional[str] = None


def _group_id(db: Session, user: M.User, name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    g = db.scalar(select(M.WatchGroup).where(M.WatchGroup.user_id == user.id, M.WatchGroup.name == name))
    if g is None:
        g = M.WatchGroup(user_id=user.id, name=name[:40])
        db.add(g)
        db.flush()
    return g.id


@router.post("/watchlist")
def add_watch(body: WatchIn, request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    items = _add_watches(db, user, body.instrument_ids, _group_id(db, user, body.group))
    return ok({"added": len(items)}, request)


@router.delete("/watchlist/{item_id}")
def remove_watch(item_id: str, request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    w = db.get(M.WatchlistItem, item_id)
    if w is None or w.user_id != user.id:
        raise ApiError(404, "not_found", "找不到這個頁面，或你沒有查看權限。")
    db.delete(w)  # 只刪監測項目，不刪持股與已發訊號
    return ok({"removed": item_id}, request)


# ---- 匯入（CSV 或 Excel） ----------------------------------------------------
_PREVIEWS: dict[str, tuple[float, str, list[dict]]] = {}
PREVIEW_TTL = 15 * 60
MAX_BYTES, MAX_ROWS = 5 * 1024 * 1024, 1000
_SYMBOL_RE = {"JP": re.compile(r"^[0-9]{3}[0-9A-Z]$")}


def _csv_entries(raw: bytes) -> list[dict]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ApiError(422, "encoding", "請使用 UTF-8 編碼的 CSV 檔，或直接上傳 Excel（.xlsx）。")
    reader = csv.DictReader(io.StringIO(text))
    need = {"market", "symbol"}
    if not reader.fieldnames or not need <= {f.strip().lower() for f in reader.fieldnames}:
        raise ApiError(422, "header", "CSV 標頭需包含 market, symbol（可加 group, fast, slow, signal）。")
    out = []
    for n, r in enumerate(reader, start=2):
        if len(out) >= MAX_ROWS:
            raise ApiError(422, "too_many_rows", "超過 1,000 列上限。")
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        out.append({"line": n, "where": f"第 {n} 列", "market": r.get("market", "").upper(), "symbol": r.get("symbol", ""),
                    "group": r.get("group") or None, "params": [r.get(k) for k in ("fast", "slow", "signal")]})
    return out


def _xlsx_entries(raw: bytes) -> list[dict]:
    try:
        rows = watch_xlsx.parse_xlsx(raw, MAX_ROWS)
    except Exception:
        raise ApiError(422, "xlsx", "讀不到這個 Excel 檔，請確認是 .xlsx 格式（舊版 .xls 請先另存成 .xlsx）。")
    if len(rows) > MAX_ROWS:
        raise ApiError(422, "too_many_rows", "超過 1,000 檔上限。")
    if not rows:
        raise ApiError(422, "header", "找不到股票。工作表需要有「股號」欄位的標頭列。")
    return rows


def _open_provider(market: str):
    """可以接受清單外代號的行情來源（例如 J-Quants 日股），讓匯入時自動新增股票。"""
    p = get_provider(market)
    return p if hasattr(p, "make_instrument") else None


@router.post("/watchlist/import-preview")
async def import_preview(request: Request, file: UploadFile = File(...), user: M.User = Depends(current_user),
                         db: Session = Depends(get_db)):
    raw = await file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ApiError(422, "file_too_large", "檔案超過 5 MB 上限。")
    is_xlsx = raw[:2] == b"PK" or (file.filename or "").lower().endswith((".xlsx", ".xlsm"))
    entries = _xlsx_entries(raw) if is_xlsx else _csv_entries(raw)
    existing = set(db.scalars(select(M.WatchlistItem.instrument_id).where(M.WatchlistItem.user_id == user.id)))
    providers: dict[str, object] = {}
    rows: list[dict] = []
    seen: set[str] = set()
    for n, e in enumerate(entries, start=1):
        row = {"line": e.get("line", n), "where": e["where"], "market": e["market"], "symbol": e["symbol"], "group": e.get("group"),
               "name": e.get("name") or None}
        inst = db.scalar(select(M.Instrument).where(M.Instrument.market_id == row["market"], M.Instrument.symbol == row["symbol"],
                                                    M.Instrument.delisted_on.is_(None)))
        params = e.get("params") or []
        error = None
        if row["market"] not in ("TW", "US", "JP"):
            error = "市場需為 TW、US 或 JP"
        elif inst is None:
            if row["market"] not in providers:
                providers[row["market"]] = _open_provider(row["market"])
            pat = _SYMBOL_RE.get(row["market"])
            if providers[row["market"]] is None or (pat and not pat.match(row["symbol"])):
                error = "找不到此股票代碼"
            else:
                row["new"] = True  # 提交時新增到股票清單
        elif any(params):
            try:
                MacdParams(*(int(p) for p in params)).validate()
                row["params"] = [int(p) for p in params]
            except (TypeError, ValueError) as ex:
                error = str(ex) if "週數" in str(ex) else "MACD 參數需為三個整數"
        key = inst.id if inst else f"{row['market']}:{row['symbol']}"
        if error:
            row["kind"], row["reason"] = "error", error
        elif key in existing or key in seen:
            row["kind"], row["reason"] = "duplicate", "已在自選中或檔案內重複"
            if inst:
                row["instrument_id"] = inst.id
        else:
            row["kind"] = "ok"
            if inst:
                row["instrument_id"], row["name"] = inst.id, inst.name
            seen.add(key)
        rows.append(row)
    pid = str(uuid.uuid4())
    _PREVIEWS[pid] = (time.time(), user.id, rows)
    ok_count = sum(1 for r in rows if r["kind"] == "ok")
    limit = plan_of(db, user).watch_limit
    return ok({"preview_id": pid, "rows": rows, "used": len(existing), "limit": limit,
               "over_by": max(0, len(existing) + ok_count - limit)}, request)


class CommitIn(BaseModel):
    preview_id: str
    selected_lines: list[int]


def _create_instrument(db: Session, market: str, symbol: str, name: Optional[str]) -> str:
    p = _open_provider(market)
    if p is None:
        raise ApiError(422, "unknown_instrument", "找不到指定的股票。")
    info = p.make_instrument(symbol, name)
    inst = db.scalar(select(M.Instrument).where(M.Instrument.mic == info.mic, M.Instrument.symbol == info.symbol))
    if inst is None:
        inst = M.Instrument(market_id=market, mic=info.mic, symbol=info.symbol, name=info.name,
                            currency=info.currency, lot_size=info.lot_size, license_status=p.source)
        db.add(inst)
        db.flush()
    elif inst.delisted_on is not None:
        inst.delisted_on = None
        inst.license_status = p.source
    return inst.id


@router.post("/watchlist/import-commit")
def import_commit(body: CommitIn, request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    entry = _PREVIEWS.get(body.preview_id)
    if entry is None or entry[1] != user.id or time.time() - entry[0] > PREVIEW_TTL:
        raise ApiError(409, "preview_expired", "預覽已過期，請重新上傳檔案。")
    rows = [dict(r) for r in entry[2] if r["line"] in set(body.selected_lines) and r["kind"] == "ok"]
    # 全部成功或全部不寫：任何錯誤由交易回滾
    for r in rows:
        if r.get("new"):
            r["instrument_id"] = _create_instrument(db, r["market"], r["symbol"], r.get("name"))
    for grp in {r["group"] for r in rows}:
        gid = _group_id(db, user, grp)
        ids = [r["instrument_id"] for r in rows if r["group"] == grp]
        _add_watches(db, user, ids, gid)
    ew = next_week_monday(date.today())
    for r in rows:
        if r.get("params"):
            f, s, g = r["params"]
            db.add(M.RuleVersion(user_id=user.id, instrument_id=r["instrument_id"], fast=f, slow=s, signal=g, effective_week=ew))
    _PREVIEWS.pop(body.preview_id, None)
    return ok({"added": len(rows)}, request)


# ---- 規則 -----------------------------------------------------------------
class RuleIn(BaseModel):
    fast: int
    slow: int
    signal: int
    rvol_up: float = 1.20
    version: Optional[int] = None  # If-Match：防覆寫


def _rule_dict(r: Optional[M.RuleVersion]) -> dict:
    if r is None:
        return {"fast": 12, "slow": 26, "signal": 9, "rvol_up": 1.20, "version": 0, "effective_week": None, "custom": False}
    return {"fast": r.fast, "slow": r.slow, "signal": r.signal, "rvol_up": r.rvol_up, "version": r.version,
            "effective_week": str(r.effective_week), "custom": r.instrument_id is not None, "id": r.id}


def _latest_rule(db: Session, user_id: str, instrument_id: Optional[str]) -> Optional[M.RuleVersion]:
    cond = M.RuleVersion.instrument_id == instrument_id if instrument_id else M.RuleVersion.instrument_id.is_(None)
    return db.scalar(select(M.RuleVersion).where(M.RuleVersion.user_id == user_id, cond)
                     .order_by(M.RuleVersion.version.desc()).limit(1))


@router.get("/rules/{scope}")
def get_rule(scope: str, request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    """scope = global 或 instrument_id。"""
    iid = None if scope == "global" else scope
    r = _latest_rule(db, user.id, iid)
    base = _latest_rule(db, user.id, None)
    return ok({"rule": _rule_dict(r) if (r or iid is None) else {**_rule_dict(base), "custom": False},
               "global": _rule_dict(base)}, request)


@router.put("/rules/{scope}")
def put_rule(scope: str, body: RuleIn, request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    iid = None if scope == "global" else scope
    if iid and db.get(M.Instrument, iid) is None:
        raise ApiError(404, "not_found", "找不到這個頁面，或你沒有查看權限。")
    errors = {}
    try:
        MacdParams(body.fast, body.slow, body.signal).validate()
    except ValueError as e:
        errors["slow" if "慢線" in str(e) else "signal" if "訊號" in str(e) else "fast"] = str(e)
    if not (1.0 <= body.rvol_up <= 3.0):
        errors["rvol_up"] = "放量門檻需介於 1.00～3.00。"
    if errors:
        raise ApiError(422, "invalid_rule", next(iter(errors.values())), errors)
    cur = _latest_rule(db, user.id, iid)
    cur_ver = cur.version if cur else 0
    if body.version is not None and body.version != cur_ver:
        raise ApiError(409, "version_conflict", "規則已在其他地方被修改，請重新載入後再試。")
    ew = next_week_monday(date.today())  # 規則異動於下一交易週生效
    r = M.RuleVersion(user_id=user.id, instrument_id=iid, fast=body.fast, slow=body.slow, signal=body.signal,
                      rvol_up=body.rvol_up, effective_week=ew, version=cur_ver + 1)
    db.add(r)
    db.flush()
    return ok({"rule": _rule_dict(r), "message": f"已儲存。新規則將於 {ew.month:02d}/{ew.day:02d} 當週生效。"}, request)


@router.delete("/rules/{instrument_id}")
def reset_rule(instrument_id: str, request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    """恢復預設：以全域規則建立一筆新版本（保留歷史）。"""
    base = _latest_rule(db, user.id, None)
    cur = _latest_rule(db, user.id, instrument_id)
    if cur is None:
        return ok({"message": "此股票已使用預設規則。"}, request)
    ew = next_week_monday(date.today())
    p = _rule_dict(base)
    db.add(M.RuleVersion(user_id=user.id, instrument_id=instrument_id, fast=p["fast"], slow=p["slow"], signal=p["signal"],
                         rvol_up=p["rvol_up"], effective_week=ew, version=cur.version + 1))
    return ok({"message": f"已恢復預設，將於 {ew.month:02d}/{ew.day:02d} 當週生效。"}, request)


# ---- 我的偏好與引導 ----------------------------------------------------------
def _prefs(db: Session, user: M.User) -> M.NotificationPrefs:
    p = db.get(M.NotificationPrefs, user.id)
    if p is None:
        p = M.NotificationPrefs(user_id=user.id, muted_codes=[], merge_threshold=5, weekly_summary=True)
        db.add(p)
        db.flush()
    return p


def me_dict(db: Session, user: M.User) -> dict:
    p = _prefs(db, user)
    b = db.get(M.LineBinding, user.id)
    return {
        "id": user.id, "email": user.email, "display_name": user.display_name, "role": user.role,
        "theme": user.theme, "updown_color": user.updown_color, "table_density": user.table_density,
        "timezone": user.timezone, "consent_version": user.consent_version, "consent_required": CONSENT_VERSION,
        "onboarding_done": user.onboarding_done,
        "plan": {"id": plan_of(db, user).id, "name": plan_of(db, user).name},
        "line": {"status": b.status if b else "none", "linked_at": b.linked_at.isoformat() if b and b.linked_at else None},
        "notify": {"muted_codes": p.muted_codes or [], "merge_threshold": p.merge_threshold,
                   "weekly_summary": p.weekly_summary, "paused_until": str(p.paused_until) if p.paused_until else None,
                   "mandatory_codes": sorted(MANDATORY_CODES)},
    }


@router.get("/me")
def me(request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    return ok(me_dict(db, user), request)


class PrefsIn(BaseModel):
    theme: Optional[str] = None
    updown_color: Optional[str] = None
    table_density: Optional[str] = None
    display_name: Optional[str] = None
    muted_codes: Optional[list[str]] = None
    merge_threshold: Optional[int] = None
    weekly_summary: Optional[bool] = None
    paused_until: Optional[date] = None
    clear_pause: bool = False


@router.put("/me/preferences")
def put_prefs(body: PrefsIn, request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    errs = {}
    if body.theme is not None:
        if body.theme not in ("system", "light", "dark"):
            errs["theme"] = "主題選項不正確。"
        user.theme = body.theme
    if body.updown_color is not None:
        if body.updown_color not in ("red_up", "green_up"):
            errs["updown_color"] = "漲跌色選項不正確。"
        user.updown_color = body.updown_color
    if body.table_density is not None:
        user.table_density = "compact" if body.table_density == "compact" else "normal"
    if body.display_name is not None:
        user.display_name = body.display_name[:80]
    p = _prefs(db, user)
    if body.muted_codes is not None:
        bad = [c for c in body.muted_codes if c not in SIGNAL_DEFS]
        if bad:
            errs["muted_codes"] = "訊號代碼不正確。"
        if set(body.muted_codes) & MANDATORY_CODES:
            errs["muted_codes"] = "「賣壓／買方首次減弱」依政策不可關閉。"
        p.muted_codes = sorted(set(body.muted_codes))
    if body.merge_threshold is not None:
        if not (3 <= body.merge_threshold <= 10):
            errs["merge_threshold"] = "合併門檻需介於 3～10。"
        p.merge_threshold = body.merge_threshold
    if body.weekly_summary is not None:
        p.weekly_summary = body.weekly_summary
    if body.clear_pause:
        p.paused_until = None
    elif body.paused_until is not None:
        p.paused_until = body.paused_until
    if errs:
        raise ApiError(422, "invalid_prefs", next(iter(errs.values())), errs)
    return ok({**me_dict(db, user), "message": "已儲存。"}, request)


@router.post("/me/consent")
def consent(request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    user.consent_version = CONSENT_VERSION
    audit(db, user, "consent", f"user:{user.id}", {"version": CONSENT_VERSION})
    return ok(me_dict(db, user), request)


@router.post("/me/onboarding-done")
def onboarding_done(request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    user.onboarding_done = True
    return ok(me_dict(db, user), request)


# ---- 會員方案 --------------------------------------------------------------
@router.get("/me/membership")
def membership(request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    plan = plan_of(db, user)
    m = db.get(M.Membership, user.id)
    used = db.scalar(select(func.count()).select_from(M.WatchlistItem).where(M.WatchlistItem.user_id == user.id))
    return ok({
        "plan": plan_dict(plan),
        "current_period_end": m.current_period_end.isoformat() if m and m.current_period_end and plan.id == m.plan else None,
        "usage": {"watchlist": used},
        "plans": [plan_dict(p) for p in PLANS.values()],
        "payments_enabled": any(p.purchasable for p in PLANS.values()),
    }, request)
