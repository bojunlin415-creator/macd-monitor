"""持股與交易紀錄（規格第 5 章）。修改以沖銷紀錄呈現，不覆寫歷程。"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as M
from app.engine import positions as P
from app.api.deps import ApiError, current_user, get_db, ok
from app.api.me import instrument_dict

router = APIRouter(prefix="/positions", tags=["positions"])


def _txs(db: Session, user_id: str, instrument_id: str) -> list[M.Transaction]:
    return db.scalars(select(M.Transaction).where(M.Transaction.user_id == user_id, M.Transaction.instrument_id == instrument_id,
                                                  M.Transaction.voided_by.is_(None)).order_by(M.Transaction.date, M.Transaction.created_at)).all()


def _to_engine(t: M.Transaction) -> P.Transaction:
    return P.Transaction(t.type, t.date, t.qty or 0, t.price or 0, t.fee or 0, t.split_ratio or 1)


def position_for(db: Session, user_id: str, instrument_id: str) -> P.Position:
    return P.replay([_to_engine(t) for t in _txs(db, user_id, instrument_id)])


def _last_raw_close(db: Session, instrument_id: str) -> Optional[float]:
    return db.scalar(select(M.DailyBarRow.close).where(M.DailyBarRow.instrument_id == instrument_id)
                     .order_by(M.DailyBarRow.date.desc(), M.DailyBarRow.revision.desc()).limit(1))


def _latest_signal(db: Session, user_id: str, instrument_id: str) -> Optional[dict]:
    e = db.scalar(select(M.SignalEvent).where(M.SignalEvent.user_id == user_id, M.SignalEvent.instrument_id == instrument_id,
                                              M.SignalEvent.is_primary.is_(True), M.SignalEvent.status.in_(("provisional", "confirmed")))
                  .order_by(M.SignalEvent.week_key.desc()).limit(1))
    return {"id": e.id, "code": e.code, "level": e.level, "direction": e.direction, "status": e.status,
            "week_key": str(e.week_key)} if e else None


def position_dict(db: Session, user: M.User, inst: M.Instrument) -> dict:
    p = position_for(db, user.id, inst.id)
    last = _last_raw_close(db, inst.id)
    u = P.unrealized(p, last)
    watched = db.scalar(select(M.WatchlistItem.id).where(M.WatchlistItem.user_id == user.id, M.WatchlistItem.instrument_id == inst.id))
    return {"instrument": instrument_dict(inst), "quantity": p.quantity, "avg_cost": p.avg_cost, "cost_basis": p.cost_basis,
            "realized_pnl": p.realized_pnl, "last_close": last, **u, "watched": bool(watched),
            "signal": _latest_signal(db, user.id, inst.id)}


@router.get("")
def list_positions(request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    ids = db.scalars(select(M.Transaction.instrument_id).where(M.Transaction.user_id == user.id).distinct()).all()
    items = [position_dict(db, user, db.get(M.Instrument, i)) for i in ids]
    items = [i for i in items if i["quantity"] > 0]
    # 集中度：同幣別內計算；缺價標「不完整」
    by_ccy: dict[str, float] = {}
    incomplete: set[str] = set()
    for i in items:
        ccy = i["instrument"]["currency"]
        if i["market_value"] is None:
            incomplete.add(ccy)
        else:
            by_ccy[ccy] = by_ccy.get(ccy, 0) + i["market_value"]
    for i in items:
        tot = by_ccy.get(i["instrument"]["currency"])
        i["concentration"] = (i["market_value"] / tot) if (tot and i["market_value"] is not None) else None
    return ok({"items": items, "totals": [{"currency": k, "market_value": v, "incomplete": k in incomplete} for k, v in by_ccy.items()]}, request)


class TxIn(BaseModel):
    instrument_id: str
    type: str = Field(pattern="^(opening|buy|sell|split)$")
    date: date
    qty: float = 0
    price: float = 0
    fee: float = 0
    split_ratio: float = 1
    note: str = ""


@router.post("/transactions")
def add_tx(body: TxIn, request: Request, idempotency_key: str = Header(..., alias="Idempotency-Key"),
           user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    inst = db.get(M.Instrument, body.instrument_id)
    if inst is None:
        raise ApiError(422, "unknown_instrument", "找不到指定的股票。")
    dup = db.scalar(select(M.Transaction).where(M.Transaction.user_id == user.id, M.Transaction.idempotency_key == idempotency_key))
    if dup:  # 重複提交：回傳相同結果，不重複記帳
        return ok({"position": position_dict(db, user, inst), "transaction_id": dup.id, "duplicate": True}, request)
    errs = {}
    if body.type != "split" and body.qty <= 0:
        errs["qty"] = "數量必須大於 0。"
    if body.type in ("buy", "sell", "opening") and body.price <= 0:
        errs["price"] = "價格必須大於 0。"
    if body.fee < 0:
        errs["fee"] = "費用不可為負數。"
    if body.type == "split" and body.split_ratio <= 0:
        errs["split_ratio"] = "拆併股比例必須大於 0。"
    if errs:
        raise ApiError(422, "invalid_tx", next(iter(errs.values())), errs)
    tx = M.Transaction(user_id=user.id, instrument_id=inst.id, type=body.type, date=body.date, qty=body.qty, price=body.price,
                       fee=body.fee, split_ratio=body.split_ratio, currency=inst.currency, note=body.note[:200],
                       idempotency_key=idempotency_key)
    try:
        P.replay([_to_engine(t) for t in _txs(db, user.id, inst.id)] + [_to_engine(tx)])
    except P.OversellError:
        raise ApiError(422, "oversell", "賣出數量超過持有數量。", {"qty": "賣出數量超過持有數量。"})
    db.add(tx)
    db.flush()
    return ok({"position": position_dict(db, user, inst), "transaction_id": tx.id}, request)


@router.get("/{instrument_id}/transactions")
def list_tx(instrument_id: str, request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(M.Transaction).where(M.Transaction.user_id == user.id, M.Transaction.instrument_id == instrument_id)
                      .order_by(M.Transaction.date, M.Transaction.created_at)).all()
    return ok([{"id": t.id, "type": t.type, "date": str(t.date), "qty": t.qty, "price": t.price, "fee": t.fee,
                "split_ratio": t.split_ratio, "currency": t.currency, "note": t.note, "voided": t.voided_by is not None}
               for t in rows], request)


@router.post("/transactions/{tx_id}/void")
def void_tx(tx_id: str, request: Request, user: M.User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(M.Transaction, tx_id)
    if t is None or t.user_id != user.id:
        raise ApiError(404, "not_found", "找不到這個頁面，或你沒有查看權限。")
    remaining = [x for x in _txs(db, user.id, t.instrument_id) if x.id != t.id]
    try:
        P.replay([_to_engine(x) for x in remaining])
    except P.OversellError:
        raise ApiError(422, "oversell", "沖銷後會造成賣超，請先沖銷較晚的賣出紀錄。")
    t.voided_by = f"void:{user.id}"
    return ok({"position": position_dict(db, user, db.get(M.Instrument, t.instrument_id))}, request)
