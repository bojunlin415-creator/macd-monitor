// 介面文案對照表（規格 9.7）。內部值一律經此轉為介面文字；新增狀態必須先加入本表。
export const SIGNAL_NAME: Record<string, string> = {
  NEG_WEAK: "賣壓首次減弱",
  CROSS: "金叉（未達候選）",
  POS_WEAK: "買方首次減弱",
  RISK: "弱化確認",
  ENTRY: "進場候選",
  EXIT: "出場候選",
};
export const SIGNAL_SENTENCE: Record<string, string> = {
  NEG_WEAK: "賣壓動能首次減弱",
  CROSS: "DIF 向上穿越 DEA，尚未符合進場候選",
  POS_WEAK: "買方動能首次減弱",
  RISK: "買方減弱後出現放量下跌或跌破 MA20",
  ENTRY: "金叉且站上 MA20、價漲放量",
  EXIT: "死叉或弱化背景下放量跌破 MA20",
};
export const LEVEL: Record<string, string> = { observe: "觀察", attention: "注意", warning: "警示", candidate: "候選" };
export const DIRECTION: Record<string, string> = { bullish: "偏多", bearish: "偏空" };
export const ARROW: Record<string, string> = { bullish: "↗", bearish: "↘" };
export const EVENT_STATUS: Record<string, string> = {
  provisional: "本週暫定",
  provisional_inactive: "暫不成立",
  confirmed: "已確認",
  invalidated: "已失效",
  superseded: "已更正",
};
export const WEEK_STATUS: Record<string, string> = { provisional: "本週暫定", pending: "待確認", confirmed: "已確認" };
export const INSTRUMENT_STATE: Record<string, string> = {
  backfilling: "準備中：補抓歷史資料",
  warmup: "暖機中",
  quality_failed: "無法判斷：資料不完整",
  ready: "監測中",
};
export const DATA_STATE: Record<string, string> = { ok: "正常", stale: "延遲", error: "異常" };
export const OUTBOX_STATUS: Record<string, string> = {
  pending: "等待發送",
  sending: "發送中",
  accepted: "LINE 已接收（不代表已讀）",
  retry: "稍後重試",
  failed: "發送失敗",
};
export const OUTBOX_KIND: Record<string, string> = { event: "事件", confirm: "週末結論", correction: "資料更正", summary: "每週摘要", test: "測試訊息" };
export const JOB_STATUS: Record<string, string> = { queued: "排隊中", running: "執行中", done: "已完成", failed: "失敗", canceled: "已取消" };
export const CONDITION: Record<string, string> = {
  pos_shrink: "正柱縮短",
  neg_shrink: "負柱縮短",
  golden_cross: "金叉",
  death_cross: "死叉",
  price_up: "價漲",
  above_ma20: "站上 MA20",
  break_ma20: "跌破 MA20",
  volume_up: "放量",
  volume_down_price: "放量下跌",
  weak_background: "弱化背景",
};
export const CONDITION_STATE: Record<string, string> = { met: "已符合", not_met: "未符合", no_data: "資料不足" };
export const CONDITION_ICON: Record<string, string> = { met: "✓", not_met: "✕", no_data: "？" };
export const MARKET: Record<string, string> = { TW: "台股", US: "美股", JP: "日股" };
export const TX_TYPE: Record<string, string> = { opening: "開倉餘額", buy: "買入", sell: "賣出", split: "拆併股" };
export const ROLE: Record<string, string> = { admin: "管理員", member: "會員" };
export const USER_STATUS: Record<string, string> = { active: "啟用", pending: "待核准", disabled: "停用" };
export const PLAN: Record<string, string> = { free: "免費會員", pro: "付費會員" };
export const RISK_NOTE = "本資訊為週線技術分析，非交易指令。";
export const WEAKEN_NOTE = "「減弱」指柱體動能，不代表價格必然反向。";
export const DISCLAIMER =
  "本系統提供週線技術資訊與歷史分析，不自動下單。暫定訊號可能於週末失效，歷史表現不代表未來報酬。請留意資料截止時間、來源限制與自身風險承受能力。";
export const LEVEL_OF: Record<string, string> = {
  NEG_WEAK: "observe", CROSS: "observe", POS_WEAK: "attention", RISK: "warning", ENTRY: "candidate", EXIT: "candidate",
};

const WD = "日一二三四五六";
export function fmtDate(iso?: string | null): string {
  if (!iso) return "資料不足";
  const d = new Date(iso.length <= 10 ? iso + "T00:00:00" : iso);
  return `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")}（${WD[d.getDay()]}）`;
}
export function fmtNum(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "資料不足";
  return x.toLocaleString("zh-TW", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
export function fmtPct(x: number | null | undefined): string {
  if (x === null || x === undefined) return "資料不足";
  const s = (x * 100).toFixed(2);
  return (x > 0 ? "+" : "") + s + "%";
}
export function weekTitle(weekKey: string): string {
  const d = new Date(weekKey + "T00:00:00");
  const end = new Date(d);
  end.setDate(d.getDate() + 4);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${isoYear(d)} 年第 ${isoWeek(d)} 週（${p(d.getMonth() + 1)}/${p(d.getDate())}～${p(end.getMonth() + 1)}/${p(end.getDate())}）`;
}
function isoWeek(d: Date): number {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - day);
  const y0 = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  return Math.ceil(((t.getTime() - y0.getTime()) / 86400000 + 1) / 7);
}
function isoYear(d: Date): number {
  const t = new Date(d);
  t.setDate(d.getDate() + 3 - ((d.getDay() + 6) % 7));
  return t.getFullYear();
}
