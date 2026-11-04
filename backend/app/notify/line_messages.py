"""LINE 訊息版型（規格 10.2、10.3）。

採 Flex Message 三層卡片；不支援時退回純文字，內容相同。
同一使用者、同一市場、同一批次達合併門檻（預設 5，可調 3～10）時合併為一則清單訊息；
合併不延後發送，每則事件仍各自寫入去重紀錄（由 outbox 層負責）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Sequence

from app.engine.signals import SIGNAL_DEFS, sort_by_priority, PRIORITY
from app.i18n import zh_tw as T

DEFAULT_MERGE_THRESHOLD = 5
MERGE_MIN, MERGE_MAX = 3, 10

COLORS = {  # 9.6 淺色主題色碼；LINE 不支援深色切換
    "observe": "#1D5FD1",
    "attention": "#9A5B00",
    "warning": "#A1176B",
    "candidate": "#4338CA",
    "text": "#142C45",
    "sub": "#4A5B6E",
    "primary": "#00737A",
}


@dataclass
class ConditionLine:
    key: str
    state: str  # met / not_met / no_data


@dataclass
class NotificationItem:
    market: str  # TW / US / JP
    symbol: str
    name: str
    code: str  # 主標籤代碼
    kind: str  # event / confirm / correction
    status: str  # provisional / confirmed / invalidated / superseded
    close: Optional[float] = None
    week_change: Optional[float] = None  # 比例，0.012 = 1.2%
    h_prev: Optional[float] = None
    h: Optional[float] = None
    rvol: Optional[float] = None
    dif: Optional[float] = None
    dea: Optional[float] = None
    params: tuple[int, int, int] = (12, 26, 9)
    elapsed: int = 0
    expected: int = 0
    week_no: int = 0
    as_of: Optional[date] = None
    conditions: list[ConditionLine] = field(default_factory=list)
    detail_url: str = ""
    chart_url: str = ""
    also_codes: list[str] = field(default_factory=list)


def _num(x: Optional[float], digits: int = 2) -> str:
    return "資料不足" if x is None else f"{x:,.{digits}f}"


def _level_label(code: str) -> str:
    level, _ = SIGNAL_DEFS[code]
    return T.SIGNAL_NAME[code] if level == "candidate" else T.LEVEL[level]


def _who(item: NotificationItem) -> str:
    return f"{T.MARKET[item.market]} {item.symbol} {item.name}"


def preview_line(item: NotificationItem) -> str:
    _, direction = SIGNAL_DEFS[item.code]
    arrow = T.DIRECTION_ARROW[direction]
    if item.kind == "event":
        return f"【{_level_label(item.code)}｜本週暫定】{_who(item)}：{T.SIGNAL_SENTENCE[item.code]} {arrow}"
    if item.kind == "confirm" and item.status == "confirmed":
        return f"【已確認】{_who(item)}：{T.SIGNAL_NAME[item.code]}，本週收盤後條件仍成立 {arrow}"
    if item.kind == "confirm":
        return f"【已失效】{_who(item)}：{T.SIGNAL_NAME[item.code]}，本週收盤後條件不再成立"
    return f"【資料更正】{_who(item)}：先前「{T.SIGNAL_NAME[item.code]}」訊號已更新"


def key_numbers(item: NotificationItem) -> str:
    wc = "資料不足" if item.week_change is None else T.fmt_pct(item.week_change)
    return (
        f"收盤 {_num(item.close)}（週 {wc}）｜柱體 {_num(item.h_prev)} → {_num(item.h)}"
        f"｜量比 {_num(item.rvol)}"
    )


def condition_text(item: NotificationItem) -> str:
    return " ".join(f"{T.CONDITION_ICON[c.state]} {T.CONDITION[c.key]}" for c in item.conditions)


def detail_text(item: NotificationItem) -> str:
    f, s, g = item.params
    as_of = f"截至 {T.fmt_date(item.as_of)} 盤後" if item.as_of else ""
    return (
        f"MACD {f}/{s}/{g}；DIF {_num(item.dif)}、DEA {_num(item.dea)}；"
        f"已完成 {item.elapsed}／{item.expected} 時段；第 {item.week_no} 週，{as_of}"
    )


def risk_text(item: NotificationItem) -> str:
    if item.status in ("provisional", "provisional_inactive"):
        return T.PROVISIONAL_RISK
    return T.RISK_NOTE


def to_text(item: NotificationItem) -> str:
    lines = [preview_line(item), key_numbers(item)]
    if item.conditions:
        lines.append(condition_text(item))
    if item.also_codes:
        lines.append("也符合：" + "、".join(T.SIGNAL_NAME[c] for c in item.also_codes))
    lines += [detail_text(item), T.WEAKEN_NOTE, risk_text(item)]
    if item.detail_url:
        lines.append(f"查看原因：{item.detail_url}")
    return "\n".join(lines)


def _txt(text: str, **kw) -> dict:
    return {"type": "text", "text": text, "wrap": True, "size": kw.pop("size", "sm"), "color": kw.pop("color", COLORS["text"]), **kw}


def _buttons(pairs: Sequence[tuple[str, str]]) -> list[dict]:
    return [
        {"type": "button", "style": "link", "height": "sm", "color": COLORS["primary"],
         "action": {"type": "uri", "label": label, "uri": uri}}
        for label, uri in pairs[:2] if uri  # 最多兩個按鈕
    ]


def to_flex(item: NotificationItem) -> dict:
    level, _ = SIGNAL_DEFS[item.code]
    body = [
        _txt(preview_line(item), weight="bold", size="md", color=COLORS[level]),
        _txt(key_numbers(item)),
    ]
    if item.conditions:
        body.append(_txt(condition_text(item)))
    if item.also_codes:
        body.append(_txt("也符合：" + "、".join(T.SIGNAL_NAME[c] for c in item.also_codes), color=COLORS["sub"]))
    body += [
        {"type": "separator", "margin": "md"},
        _txt(detail_text(item), size="xs", color=COLORS["sub"]),
        _txt(T.WEAKEN_NOTE + risk_text(item), size="xs", color=COLORS["sub"]),
    ]
    bubble: dict = {"type": "bubble", "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body}}
    btns = _buttons([("查看原因", item.detail_url), ("開啟圖表", item.chart_url)])
    if btns:
        bubble["footer"] = {"type": "box", "layout": "horizontal", "contents": btns}
    return {"type": "flex", "altText": preview_line(item)[:400], "contents": bubble}


def merged_preview(market: str, items: Sequence[NotificationItem]) -> str:
    counts: dict[str, int] = {}
    for it in items:
        lv = SIGNAL_DEFS[it.code][0]
        counts[lv] = counts.get(lv, 0) + 1
    order = ["candidate", "warning", "attention", "observe"]
    summary = "、".join(f"{T.LEVEL[lv]} {counts[lv]}" for lv in order if lv in counts)
    return f"【{T.MARKET[market]}盤後】本次 {len(items)} 檔新訊號：{summary}"


def _sorted_items(items: Sequence[NotificationItem]) -> list[NotificationItem]:
    return sorted(items, key=lambda i: PRIORITY.index(i.code))


def merged_line(item: NotificationItem) -> str:
    _, direction = SIGNAL_DEFS[item.code]
    return f"{_level_label(item.code)}{T.DIRECTION_ARROW[direction]} {item.symbol} {item.name}：{T.SIGNAL_SENTENCE[item.code]}"


def merged_to_text(market: str, items: Sequence[NotificationItem], list_url: str = "") -> str:
    lines = [merged_preview(market, items)] + [merged_line(i) for i in _sorted_items(items)]
    lines.append(T.RISK_NOTE)
    if list_url:
        lines.append(f"查看全部：{list_url}")
    return "\n".join(lines)


def merged_to_flex(market: str, items: Sequence[NotificationItem], list_url: str = "") -> dict:
    body = [_txt(merged_preview(market, items), weight="bold", size="md")]
    for it in _sorted_items(items):
        body.append(_txt(merged_line(it), color=COLORS[SIGNAL_DEFS[it.code][0]]))
    body.append(_txt(T.RISK_NOTE, size="xs", color=COLORS["sub"]))
    bubble: dict = {"type": "bubble", "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body}}
    btns = _buttons([("查看全部", list_url)])
    if btns:
        bubble["footer"] = {"type": "box", "layout": "horizontal", "contents": btns}
    return {"type": "flex", "altText": merged_preview(market, items)[:400], "contents": bubble}


@dataclass
class OutgoingMessage:
    """一則實際要送出的 LINE 訊息，對應一或多個 outbox 項目。"""

    item_indexes: list[int]
    flex: dict
    text: str


def plan_batch(
    items: Sequence[NotificationItem],
    merge_threshold: int = DEFAULT_MERGE_THRESHOLD,
    list_url: str = "",
) -> list[OutgoingMessage]:
    """同一使用者同一批次的事件，依市場決定分開送或合併（UAT-22）。

    只有「事件」種類會合併；確認／失效／更正一律逐則送出以明確對應先前訊息。
    """
    if not (MERGE_MIN <= merge_threshold <= MERGE_MAX):
        raise ValueError("合併門檻需介於 3～10。")
    out: list[OutgoingMessage] = []
    by_market: dict[str, list[int]] = {}
    for idx, it in enumerate(items):
        if it.kind == "event":
            by_market.setdefault(it.market, []).append(idx)
        else:
            out.append(OutgoingMessage([idx], to_flex(it), to_text(it)))
    for market, idxs in by_market.items():
        if len(idxs) >= merge_threshold:
            group = [items[i] for i in idxs]
            out.append(OutgoingMessage(idxs, merged_to_flex(market, group, list_url), merged_to_text(market, group, list_url)))
        else:
            out += [OutgoingMessage([i], to_flex(items[i]), to_text(items[i])) for i in idxs]
    return out


def weekly_summary_text(week_no: int, confirmed: int, invalidated: int, holding_risk: int,
                        market_cutoffs: dict[str, Optional[date]], missing: Sequence[str]) -> str:
    lines = [f"【每週摘要｜第 {week_no} 週】已確認 {confirmed}、失效 {invalidated}、持股風險 {holding_risk}"]
    for m, d in market_cutoffs.items():
        lines.append(f"{T.MARKET[m]}：" + (f"資料截至 {T.fmt_date(d)} 收盤" if d else "資料延遲，補齊後更新站內摘要"))
    if missing:
        lines.append("無法判斷（資料不完整）：" + "、".join(missing))
        lines.append("缺資料的股票不列為無風險。")
    lines.append(T.RISK_NOTE)
    return "\n".join(lines)
