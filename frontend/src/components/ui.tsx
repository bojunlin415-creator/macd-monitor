import { ReactNode, useEffect, useRef, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../lib/api";
import {
  ARROW, CONDITION, CONDITION_ICON, CONDITION_STATE, DATA_STATE, DIRECTION, EVENT_STATUS, LEVEL, MARKET,
  RISK_NOTE, SIGNAL_NAME, fmtDate, fmtNum, fmtPct,
} from "../i18n";

export interface Instrument { id: string; market: string; mic: string; symbol: string; name: string; currency: string; lot_size: number; state: string; weeks_available: number; license_status: string }
export interface SignalEvent {
  id: string; instrument: Instrument; week_key: string; week_no: number; year: number; code: string; level: string;
  direction: string; status: string; is_primary: boolean; also: string[]; sentence: string; rvol: number | null;
  close: number | null; week_change: number | null; elapsed: number | null; expected: number | null; held: boolean;
  seen: boolean; first_at: string;
}

// 訊號徽章：[等級色塊＋等級字] [↗／↘ 偏多／偏空]；候選級顯示「進場候選／出場候選」
export function SignalBadge({ code, level, direction }: { code: string; level: string; direction: string }) {
  const label = SIGNAL_NAME[code] ?? LEVEL[level];
  return (
    <span className={`badge lv-${level}`}>
      <span className="lv">{label}</span>
      <span className="dir" aria-label={DIRECTION[direction]}>{ARROW[direction]} {DIRECTION[direction]}</span>
    </span>
  );
}

export function StatusTag({ status }: { status: string }) {
  return <span className={`status ${status}`}>{EVENT_STATUS[status] ?? status}</span>;
}

export function Change({ x }: { x: number | null | undefined }) {
  if (x === null || x === undefined) return <span className="muted">資料不足</span>;
  return <span className={x > 0 ? "rise" : x < 0 ? "fall" : ""}>{fmtPct(x)}</span>;
}

export function SignalCard({ e }: { e: SignalEvent }) {
  return (
    <Link to={`/signals/${e.id}`} className={`card sigcard ${e.seen ? "seen" : ""}`}>
      <div className="l1"><SignalBadge code={e.code} level={e.level} direction={e.direction} /><StatusTag status={e.status} /></div>
      {e.held && <span className="held">持有</span>}
      <div className="l2">{e.instrument.name} <span className="muted num">{e.instrument.symbol}</span> <span className="muted small">{MARKET[e.instrument.market]}</span></div>
      <div>{e.sentence}{e.also.length > 0 && <span className="muted small">（也符合：{e.also.map((c) => SIGNAL_NAME[c]).join("、")}）</span>}</div>
      <div className="l4">
        <span>量比 {fmtNum(e.rvol)}</span>
        <span>週變動 <Change x={e.week_change} /></span>
        <span>第 {e.week_no} 週{e.status === "provisional" && e.expected ? `，已完成 ${e.elapsed}／${e.expected} 時段` : ""}</span>
      </div>
    </Link>
  );
}

export interface MarketStatus { id: string; data_cutoff: string | null; state: string }
export function DataStatusBar({ markets }: { markets: MarketStatus[] }) {
  return (
    <div className="datastatus" role="status" aria-label="資料狀態">
      {markets.map((m) => (
        <span key={m.id} className={`dchip ${m.state}`}>
          <strong>{MARKET[m.id]}</strong>
          {m.data_cutoff ? `截至 ${fmtDate(m.data_cutoff)} 收盤` : "尚無資料"}
          <span>{DATA_STATE[m.state] ?? m.state}</span>
          {m.state !== "ok" && <Link to="/help/data" className="small">了解原因</Link>}
        </span>
      ))}
    </div>
  );
}

export function QuotaBar({ used, limit }: { used: number; limit: number }) {
  const cls = used >= limit ? "full" : used >= 180 ? "warn" : "";
  return (
    <div className={`quota ${cls}`}>
      <span className="small">監測中 <strong className="num">{used}／{limit}</strong>{used >= limit && "（已達上限，請先移除再新增）"}</span>
      <div className="bar" role="progressbar" aria-valuenow={used} aria-valuemax={limit} aria-label="自選配額"><i style={{ width: `${Math.min(100, (used / limit) * 100)}%` }} /></div>
    </div>
  );
}

export function ConditionRow({ c }: { c: { key: string; state: string; actual?: number | null; threshold?: number | null } }) {
  const val = (() => {
    if (c.state === "no_data") return "";
    if (c.key === "volume_up" || c.key === "volume_down_price") return `量比 ${fmtNum(c.actual)}，門檻 ${fmtNum(c.threshold)}`;
    if (c.key === "above_ma20" || c.key === "break_ma20") return `收盤 ${fmtNum(c.actual)}，MA20 ${fmtNum(c.threshold)}`;
    if (c.key === "price_up") return `收盤 ${fmtNum(c.actual)}，前週 ${fmtNum(c.threshold)}`;
    if (c.key === "pos_shrink" || c.key === "neg_shrink") return `柱體 ${fmtNum(c.threshold, 4)} → ${fmtNum(c.actual, 4)}`;
    if (c.key === "golden_cross" || c.key === "death_cross") return `柱體 ${fmtNum(c.actual, 4)}`;
    return "";
  })();
  return (
    <div className={`cond ${c.state}`}>
      <span className="ic" aria-hidden>{CONDITION_ICON[c.state]}</span>
      <span className="st">{CONDITION_STATE[c.state]}</span>
      <span>{CONDITION[c.key] ?? c.key}</span>
      <span className="val num">{val}</span>
    </div>
  );
}

export const RiskNote = ({ text = RISK_NOTE }: { text?: string }) => <p className="risknote">{text}</p>;

export function Skeleton({ n = 3 }: { n?: number }) {
  return <div className="stack" aria-busy="true">{Array.from({ length: n }).map((_, i) => <div key={i} className="skeleton" />)}</div>;
}

export function Empty({ text, action }: { text: string; action?: ReactNode }) {
  return <div className="card empty"><div className="art" aria-hidden>📈</div><p>{text}</p>{action}</div>;
}

export function ErrorBox({ error, onRetry }: { error: ApiError | Error; onRetry?: () => void }) {
  const rid = (error as ApiError).requestId;
  return (
    <div className="banner" role="alert">
      <div>{error.message}</div>
      <div className="row small">
        {onRetry && <button className="btn" onClick={onRetry}>重新載入</button>}
        {rid && <span className="muted">追蹤碼：<code>{rid}</code></span>}
      </div>
    </div>
  );
}

// 讀取 hook：錯誤時保留最近一次成功資料
export function useApi<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [meta, setMeta] = useState<Record<string, any>>({});
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      const r = await api<T>(path);
      setData(r.data); setMeta(r.meta); setError(null);
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);
  useEffect(() => { load(); }, [load]);
  return { data, meta, error, loading, reload: load, setData };
}

export function Toast({ text, onDone }: { text: string; onDone: () => void }) {
  useEffect(() => { const t = setTimeout(onDone, 3500); return () => clearTimeout(t); }, [onDone]);
  return <div className="toast" role="status">{text}</div>;
}

// 對話框：鎖定焦點，關閉後回到觸發元件
export function Modal({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef<Element | null>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) { opener.current = document.activeElement; d.showModal(); }
    if (!open && d.open) { d.close(); (opener.current as HTMLElement | null)?.focus?.(); }
  }, [open]);
  return (
    <dialog ref={ref} className="modal" onClose={onClose} aria-label={title}>
      <h2>{title}</h2>
      {children}
    </dialog>
  );
}

export function Field({ label, error, children }: { label: string; error?: string; children: ReactNode }) {
  return <div className="field"><label>{label}</label>{children}{error && <span className="err" role="alert">{error}</span>}</div>;
}
