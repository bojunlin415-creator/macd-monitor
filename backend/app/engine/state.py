"""事件狀態流轉與去重（規格 2.5）。

純函式：輸入「既有狀態」與「本次判斷」，回傳新狀態與要排入的通知種類。
資料庫另以唯一鍵 (user_id, instrument_id, week_key, signal_code, notification_kind[, data_revision])
保證同週同訊號只通知一次，工作重跑也不會重複。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# 事件狀態（內部值；介面文字見 i18n 對照表 9.7）
NONE = None
PROVISIONAL = "provisional"  # 本週暫定
PROVISIONAL_INACTIVE = "provisional_inactive"  # 暫不成立
CONFIRMED = "confirmed"  # 已確認
INVALIDATED = "invalidated"  # 已失效
SUPERSEDED = "superseded"  # 已更正

# 通知種類（唯一鍵的一部分）
KIND_EVENT = "event"  # 事件（暫定首次成立）
KIND_CONFIRM = "confirm"  # 週末結論（確認或失效擇一）
KIND_CORRECTION = "correction"  # 資料更正

PHASE_INTRADAY = "intraday"  # 平日盤後（暫定週）
PHASE_WEEKEND = "weekend"  # 週末確認（確認週）
PHASE_REVISION = "revision"  # 已確認後資料更正重算


@dataclass(frozen=True)
class Transition:
    status: Optional[str]
    notify: Optional[str]  # 要排入的通知種類；None 表示不推播
    changed: bool


def transition(current: Optional[str], holds: bool, phase: str) -> Transition:
    """依目前狀態、本次是否成立、判斷階段，決定下一狀態。"""
    if phase == PHASE_INTRADAY:
        if current is None:
            return Transition(PROVISIONAL, KIND_EVENT, True) if holds else Transition(None, None, False)
        if current == PROVISIONAL and not holds:
            return Transition(PROVISIONAL_INACTIVE, None, True)
        if current == PROVISIONAL_INACTIVE and holds:
            # 同週取消後再成立，不重發相同首次提醒
            return Transition(PROVISIONAL, None, True)
        return Transition(current, None, False)

    if phase == PHASE_WEEKEND:
        if current is None:
            # 週末才首次成立：直接建立已確認事件，不補暫定訊息
            return Transition(CONFIRMED, KIND_CONFIRM, True) if holds else Transition(None, None, False)
        if current in (PROVISIONAL, PROVISIONAL_INACTIVE):
            return Transition(CONFIRMED if holds else INVALIDATED, KIND_CONFIRM, True)
        return Transition(current, None, False)

    if phase == PHASE_REVISION:
        if current == CONFIRMED and not holds:
            return Transition(SUPERSEDED, KIND_CORRECTION, True)
        if current == INVALIDATED and holds:
            return Transition(SUPERSEDED, KIND_CORRECTION, True)
        return Transition(current, None, False)

    raise ValueError(f"未知的判斷階段：{phase}")


def dedupe_key(
    user_id: str,
    instrument_id: str,
    week_key: str,
    signal_code: str,
    kind: str,
    data_revision: Optional[int] = None,
) -> str:
    """規則版本不納入唯一鍵，避免調參繞過「同週一次」。"""
    parts = [user_id, instrument_id, week_key, signal_code, kind]
    if kind == KIND_CORRECTION:
        parts.append(str(data_revision or 0))
    return "|".join(parts)
