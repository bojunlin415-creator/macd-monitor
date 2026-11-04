"""解析自選股 Excel（例如「日經成分股桌面」）。

格式：每個工作表有一列標頭，含「股號」與「公司名稱」（或「中文名稱」）；其餘欄位與底色不使用。
市場：有「市場」欄就用；否則看工作表名稱（日→日股、台→台股、美→美股），都沒有則當日股。
"""
from __future__ import annotations

import io
import re
from typing import Optional

CODE_HEADERS = ("股號", "代號", "股票代號", "symbol")
NAME_HEADERS = ("公司名稱", "中文名稱", "名稱", "name")
MARKET_HEADERS = ("市場", "market")
_MARKET_WORDS = {"日": "JP", "台": "TW", "臺": "TW", "美": "US"}
_MARKET_CODES = {"JP": "JP", "TW": "TW", "US": "US", "日股": "JP", "台股": "TW", "臺股": "TW", "美股": "US"}
_CODE_RE = re.compile(r"^[0-9A-Z]{1,6}$")


def _code(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip().upper()
    return s if _CODE_RE.match(s) else None


def _find(header: list, names: tuple[str, ...]) -> Optional[int]:
    norm = [str(h).strip().lower() if h is not None else "" for h in header]
    for n in names:
        if n.lower() in norm:
            return norm.index(n.lower())
    return None


def _sheet_market(title: str) -> str:
    for k, m in _MARKET_WORDS.items():
        if k in title:
            return m
    return "JP"


def parse_xlsx(raw: bytes, max_rows: int) -> list[dict]:
    """回傳每檔股票：where（工作表與列號）、market、symbol、name。"""
    from openpyxl import load_workbook  # 只在匯入 Excel 時載入

    wb = load_workbook(io.BytesIO(raw), read_only=False, data_only=True)
    out: list[dict] = []
    for ws in wb.worksheets:
        code_i = name_i = market_i = None
        for row in ws.iter_rows():
            values = [c.value for c in row]
            if code_i is None:
                code_i = _find(values, CODE_HEADERS)
                if code_i is not None:
                    name_i, market_i = _find(values, NAME_HEADERS), _find(values, MARKET_HEADERS)
                continue
            if code_i >= len(row):
                continue
            sym = _code(row[code_i].value)
            if sym is None:
                continue  # 空白、註記或圖例列
            market = _sheet_market(ws.title)
            if market_i is not None and market_i < len(row) and row[market_i].value:
                market = _MARKET_CODES.get(str(row[market_i].value).strip().upper(), str(row[market_i].value).strip().upper())
            name = str(row[name_i].value).strip() if name_i is not None and name_i < len(row) and row[name_i].value else ""
            out.append({"where": f"{ws.title} 第 {row[code_i].row} 列", "market": market, "symbol": sym,
                        "name": name})
            if len(out) > max_rows:
                return out
    return out
