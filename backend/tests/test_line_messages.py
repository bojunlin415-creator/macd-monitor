import re
from datetime import date

import pytest

from app.notify.line_messages import NotificationItem, ConditionLine, plan_batch, preview_line, to_text


def item(code="POS_WEAK", market="TW", kind="event", status="provisional", sym="0000"):
    return NotificationItem(
        market=market, symbol=sym, name="示意股", code=code, kind=kind, status=status,
        close=100.0, week_change=-0.012, h_prev=1.0, h=0.8, rvol=0.95, dif=2.4, dea=1.6,
        elapsed=3, expected=5, week_no=45, as_of=date(2026, 11, 4),
        conditions=[ConditionLine("pos_shrink", "met"), ConditionLine("volume_down_price", "not_met"), ConditionLine("break_ma20", "not_met")],
        detail_url="https://example.invalid/s/1", chart_url="https://example.invalid/c/1",
    )


def test_preview_line_matches_spec_example():
    assert preview_line(item()) == "【賣出點｜本週暫定】台股 0000 示意股：MACD 正柱首次縮短（買方減弱） ↘"


def test_text_contains_all_layers():
    t = to_text(item())
    assert "收盤 100.00（週 -1.20%）｜柱體 1.00 → 0.80｜量比 0.95" in t
    assert "✓ 正柱縮短 ✕ 放量下跌 ✕ 跌破 MA20" in t
    assert "已完成 3／5 時段" in t and "第 45 週" in t
    assert "不構成投資建議" in t


def test_confirm_and_invalidate_previews():
    assert preview_line(item(kind="confirm", status="confirmed")).startswith("【已確認】")
    assert preview_line(item(kind="confirm", status="invalidated")).startswith("【已失效】")
    assert preview_line(item(kind="correction", status="superseded")).startswith("【資料更正】")


@pytest.mark.parametrize("n,expected_msgs", [(4, 4), (5, 1), (12, 1)])
def test_uat22_merge_threshold(n, expected_msgs):
    items = [item(sym=f"{i:04d}") for i in range(n)]
    msgs = plan_batch(items)
    assert len(msgs) == expected_msgs
    covered = sorted(i for m in msgs for i in m.item_indexes)
    assert covered == list(range(n))  # 每則事件仍對應自己的去重紀錄


def test_merge_is_per_market_and_sorted_by_priority():
    items = [item(sym=f"{i}", code=c) for i, c in enumerate(["NEG_WEAK", "POS_WEAK", "EXIT", "CROSS", "RISK"])]
    items.append(item(market="US", sym="AAA"))
    msgs = plan_batch(items)
    assert len(msgs) == 2
    merged = next(m for m in msgs if len(m.item_indexes) == 5)
    assert merged.text.startswith("【台股盤後】本次 5 檔新訊號：候選 3、警示 1、觀察 1")
    assert merged.text.splitlines()[1].startswith("賣出點")
    assert merged.text.splitlines()[2].startswith("買進點")


def test_no_english_status_words():
    """UAT-21：除技術追蹤碼與股票代碼外無英文狀態字。"""
    for kind, status in [("event", "provisional"), ("confirm", "confirmed"), ("confirm", "invalidated")]:
        t = to_text(item(kind=kind, status=status, sym="0000"))
        t = re.sub(r"https?://\S+", "", t)
        words = set(re.findall(r"[A-Za-z_]{3,}", t))
        assert words <= {"MACD", "DIF", "DEA"}, words
