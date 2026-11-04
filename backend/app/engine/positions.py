"""持股：移動加權平均成本（規格第 5 章）。只用於把訊號套到持股，不計算建議買賣股數。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Sequence

BUY, SELL, OPENING, SPLIT = "buy", "sell", "opening", "split"


class OversellError(ValueError):
    pass


@dataclass(frozen=True)
class Transaction:
    type: str
    date: date
    qty: float = 0.0
    price: float = 0.0
    fee: float = 0.0
    split_ratio: float = 1.0  # 拆股：1 股變 split_ratio 股


@dataclass
class Position:
    quantity: float = 0.0
    cost_basis: float = 0.0  # 剩餘總成本
    realized_pnl: float = 0.0
    history: list[Transaction] = field(default_factory=list)

    @property
    def avg_cost(self) -> Optional[float]:
        return None if self.quantity == 0 else self.cost_basis / self.quantity

    def apply(self, tx: Transaction) -> "Position":
        if tx.type in (BUY, OPENING):
            self.cost_basis += tx.qty * tx.price + tx.fee
            self.quantity += tx.qty
        elif tx.type == SELL:
            if tx.qty > self.quantity + 1e-12:
                raise OversellError("賣出數量超過持有數量。")
            avg = self.avg_cost or 0.0
            self.realized_pnl += tx.qty * tx.price - tx.fee - tx.qty * avg
            self.cost_basis -= tx.qty * avg
            self.quantity -= tx.qty
            if abs(self.quantity) < 1e-12:
                self.quantity, self.cost_basis = 0.0, 0.0
        elif tx.type == SPLIT:
            self.quantity *= tx.split_ratio  # 總成本不變
        else:
            raise ValueError(f"未知交易類型：{tx.type}")
        self.history.append(tx)
        return self


def replay(txs: Sequence[Transaction]) -> Position:
    p = Position()
    for tx in sorted(txs, key=lambda t: t.date):
        p.apply(tx)
    return p


def unrealized(p: Position, last_raw_close: Optional[float]) -> dict:
    """市值用最新「原始」收盤；成本為零顯示「不適用」。"""
    if last_raw_close is None:
        return {"market_value": None, "unrealized": None, "return": None}
    mv = p.quantity * last_raw_close
    u = mv - p.cost_basis
    return {"market_value": mv, "unrealized": u, "return": (u / p.cost_basis) if p.cost_basis else None}
