"""介面文案對照表（規格 9.7）。系統內部值一律經此轉為介面文字；新增狀態必須先加入本表。

前端 frontend/src/i18n/zhTW.ts 維護同一份對照，後端用於 LINE 訊息與 API 錯誤訊息。
"""
from __future__ import annotations

SIGNAL_NAME = {
    "NEG_WEAK": "賣壓首次減弱",
    "CROSS": "金叉（未達候選）",
    "POS_WEAK": "買方首次減弱",
    "RISK": "弱化確認",
    "ENTRY": "進場候選",
    "EXIT": "出場候選",
}

# 一句話原因（LINE 預覽行與卡片第 3 行）
SIGNAL_SENTENCE = {
    "NEG_WEAK": "賣壓動能首次減弱",
    "CROSS": "DIF 向上穿越 DEA，尚未符合進場候選",
    "POS_WEAK": "買方動能首次減弱",
    "RISK": "買方減弱後出現放量下跌或跌破 MA20",
    "ENTRY": "金叉且站上 MA20、價漲放量",
    "EXIT": "死叉或弱化背景下放量跌破 MA20",
}

LEVEL = {
    "observe": "觀察",
    "attention": "注意",
    "warning": "警示",
    "candidate": "候選",
}

DIRECTION = {"bullish": "偏多", "bearish": "偏空"}
DIRECTION_ARROW = {"bullish": "↗", "bearish": "↘"}

EVENT_STATUS = {
    "provisional": "本週暫定",
    "provisional_inactive": "暫不成立",
    "confirmed": "已確認",
    "invalidated": "已失效",
    "superseded": "已更正",
}

WEEK_STATUS = {
    "provisional": "本週暫定",
    "pending": "待確認",
    "confirmed": "已確認",
}

INSTRUMENT_STATE = {
    "warmup": "暖機中（已累積 {n}／{w} 週）",
    "backfilling": "準備中：補抓歷史資料",
    "quality_failed": "無法判斷：資料不完整",
    "ready": "監測中",
}

DATA_STATE = {"ok": "正常", "stale": "資料延遲", "error": "異常"}

OUTBOX_STATUS = {
    "pending": "等待發送",
    "sending": "發送中",
    "accepted": "LINE 已接收（不代表已讀）",
    "retry": "稍後重試",
    "failed": "發送失敗",
}

JOB_STATUS = {
    "queued": "排隊中",
    "running": "執行中",
    "done": "已完成",
    "failed": "失敗",
    "canceled": "已取消",
}

CONDITION = {
    "pos_shrink": "正柱縮短",
    "neg_shrink": "負柱縮短",
    "golden_cross": "金叉",
    "death_cross": "死叉",
    "price_up": "價漲",
    "above_ma20": "站上 MA20",
    "break_ma20": "跌破 MA20",
    "volume_up": "放量",
    "volume_down_price": "放量下跌",
    "weak_background": "弱化背景",
}

CONDITION_STATE = {"met": "已符合", "not_met": "未符合", "no_data": "資料不足"}
CONDITION_ICON = {"met": "✓", "not_met": "✕", "no_data": "？"}

MARKET = {"TW": "台股", "US": "美股", "JP": "日股"}

TERMS = {
    "RVOL": "量比",
    "DIF": "快線（DIF）",
    "DEA": "慢線（DEA）",
    "H": "柱體",
    "censored": "觀察期未滿",
}

RISK_NOTE = "本資訊為週線技術分析，非交易指令。"
WEAKEN_NOTE = "「減弱」指柱體動能，不代表價格必然反向。"
PROVISIONAL_RISK = "週線尚未完成，訊號可能於週末失效；非交易指令。"

DISCLAIMER = (
    "本系統提供週線技術資訊與歷史分析，不自動下單。暫定訊號可能於週末失效，"
    "歷史表現不代表未來報酬。請留意資料截止時間、來源限制與自身風險承受能力。"
)

WEEKDAY = "一二三四五六日"


def fmt_date(d) -> str:
    """日期格式「11/04（三）」。"""
    return f"{d.month:02d}/{d.day:02d}（{WEEKDAY[d.weekday()]}）"


def fmt_pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def badge(code: str) -> str:
    from app.engine.signals import SIGNAL_DEFS

    level, direction = SIGNAL_DEFS[code]
    label = SIGNAL_NAME[code] if level == "candidate" else LEVEL[level]
    return f"{label} {DIRECTION_ARROW[direction]}{DIRECTION[direction]}"
