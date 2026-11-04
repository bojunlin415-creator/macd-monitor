import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError, idemKey } from "../lib/api";
import { Empty, ErrorBox, Field, Instrument, Modal, SignalBadge, Skeleton, StatusTag, Toast, useApi } from "../components/ui";
import { MARKET, TX_TYPE, fmtNum, fmtPct } from "../i18n";

interface Pos { instrument: Instrument; quantity: number; avg_cost: number | null; cost_basis: number; realized_pnl: number; last_close: number | null; market_value: number | null; unrealized: number | null; return: number | null; watched: boolean; concentration: number | null; signal: { id: string; code: string; level: string; direction: string; status: string } | null }

const unit = (i: Instrument) => (i.market === "TW" ? "股（1 張＝1,000 股）" : "股");
const pnl = (x: number | null) => x === null ? <span className="muted">不適用</span> : <span className={x > 0 ? "rise" : x < 0 ? "fall" : ""}>{x > 0 ? "+" : ""}{fmtNum(x)}</span>;

export default function Positions() {
  const { data, error, loading, reload } = useApi<{ items: Pos[]; totals: { currency: string; market_value: number; incomplete: boolean }[] }>("/positions");
  const [params] = useSearchParams();
  const [open, setOpen] = useState(!!params.get("instrument"));
  const [toast, setToast] = useState<string | null>(null);
  const [history, setHistory] = useState<Pos | null>(null);
  if (loading && !data) return <Skeleton />;
  if (!data) return error ? <ErrorBox error={error} onRetry={reload} /> : null;
  return (
    <>
      <div className="topbar"><h1>持股</h1><button className="btn primary" onClick={() => setOpen(true)}>記錄交易</button></div>
      {data.totals.length > 0 && <p className="small muted">市值合計（原幣分列）：{data.totals.map((t) => `${t.currency} ${fmtNum(t.market_value, 0)}${t.incomplete ? "（不完整）" : ""}`).join("；")}</p>}
      {data.items.length === 0 ? <Empty text="尚未記錄持股。記錄後，相關的偏空訊號會出現在總覽的「持股風險」。" action={<button className="btn primary" onClick={() => setOpen(true)}>記錄交易</button>} /> : (
        <div className="grid">
          {data.items.map((p) => (
            <div key={p.instrument.id} className="card stack" style={{ gap: 8 }}>
              <div className="row"><strong style={{ fontSize: 17, flex: 1 }}><Link to={`/instruments/${p.instrument.id}`}>{p.instrument.name}</Link> <span className="muted num">{p.instrument.symbol}</span></strong><span className="small muted">{MARKET[p.instrument.market]}・{p.instrument.currency}</span></div>
              {p.signal ? <Link to={`/signals/${p.signal.id}`} className="row" style={{ textDecoration: "none" }}><SignalBadge code={p.signal.code} level={p.signal.level} direction={p.signal.direction} /><StatusTag status={p.signal.status} /></Link>
                : p.watched ? <span className="small muted">目前無訊號</span> : <span className="small" style={{ color: "var(--attention)" }}>未監測，不會收到訊號</span>}
              <dl className="kv num">
                <dt>數量</dt><dd>{fmtNum(p.quantity, 0)} 股</dd>
                <dt>均價</dt><dd>{fmtNum(p.avg_cost)}</dd>
                <dt>市值</dt><dd>{fmtNum(p.market_value)}</dd>
                <dt>未實現損益</dt><dd>{pnl(p.unrealized)}（{p.return === null ? "不適用" : fmtPct(p.return)}）</dd>
                <dt>已實現損益</dt><dd>{pnl(p.realized_pnl)}</dd>
                <dt>集中度</dt><dd>{p.concentration === null ? "不完整" : fmtPct(p.concentration).replace("+", "")}</dd>
              </dl>
              <button className="btn link" style={{ alignSelf: "flex-start" }} onClick={() => setHistory(p)}>交易紀錄</button>
            </div>
          ))}
        </div>
      )}
      <TxModal open={open} onClose={() => setOpen(false)} presetId={params.get("instrument")} onDone={(m) => { setToast(m); reload(); }} />
      {history && <HistoryModal pos={history} onClose={() => { setHistory(null); reload(); }} />}
      {toast && <Toast text={toast} onDone={() => setToast(null)} />}
    </>
  );
}

function TxModal({ open, onClose, presetId, onDone }: { open: boolean; onClose: () => void; presetId: string | null; onDone: (m: string) => void }) {
  const [q, setQ] = useState("");
  const [opts, setOpts] = useState<Instrument[]>([]);
  const [inst, setInst] = useState<Instrument | null>(null);
  const [f, setF] = useState({ type: "buy", date: new Date().toISOString().slice(0, 10), qty: "", price: "", fee: "0", split_ratio: "2", note: "" });
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [key, setKey] = useState(idemKey());
  useEffect(() => { api<Instrument[]>(`/instruments?q=${encodeURIComponent(q)}`).then((r) => setOpts(r.data)).catch(() => {}); }, [q]);
  useEffect(() => { if (presetId) api<Instrument>(`/instruments/${presetId}`).then((r) => setInst(r.data)).catch(() => {}); }, [presetId]);
  const est = Number(f.qty) * Number(f.price) + (f.type === "sell" ? -1 : 1) * Number(f.fee || 0);
  async function save() {
    if (!inst || busy) return;
    setBusy(true); setErr(null); setErrs({});
    try {
      await api("/positions/transactions", { method: "POST", headers: { "Idempotency-Key": key },
        body: { instrument_id: inst.id, type: f.type, date: f.date, qty: Number(f.qty || 0), price: Number(f.price || 0), fee: Number(f.fee || 0), split_ratio: Number(f.split_ratio || 1), note: f.note } });
      onDone("已儲存交易，持股已依伺服器結果重算。"); setKey(idemKey()); onClose();
    } catch (e) { const a = e as ApiError; setErr(a.message); setErrs(a.fieldErrors); } finally { setBusy(false); }
  }
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  return (
    <Modal open={open} onClose={onClose} title="記錄交易">
      <div className="stack">
        {inst ? <div className="row"><strong>{inst.name} {inst.symbol}</strong><span className="muted small">{MARKET[inst.market]}・{inst.currency}</span><button className="btn link" onClick={() => setInst(null)}>更換</button></div> : <>
          <input className="input" placeholder="搜尋股票" value={q} onChange={(e) => setQ(e.target.value)} aria-label="搜尋股票" />
          <div style={{ maxHeight: 160, overflowY: "auto" }}>{opts.map((o) => <button key={o.id} className="btn link" style={{ display: "block" }} onClick={() => setInst(o)}>{o.name} {o.symbol}（{MARKET[o.market]}）</button>)}</div>
        </>}
        <Field label="類型"><select className="input" value={f.type} onChange={set("type")}>{Object.entries(TX_TYPE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <Field label="日期"><input className="input" type="date" value={f.date} onChange={set("date")} /></Field>
        {f.type === "split" ? <Field label="每 1 股變為幾股" error={errs.split_ratio}><input className="input" inputMode="decimal" value={f.split_ratio} onChange={set("split_ratio")} /></Field> : <>
          <Field label={`數量（${inst ? unit(inst) : "股"}）`} error={errs.qty}><input className={`input ${errs.qty ? "invalid" : ""}`} inputMode="decimal" value={f.qty} onChange={set("qty")} /></Field>
          <Field label={`價格（${inst?.currency ?? "原幣"}）`} error={errs.price}><input className={`input ${errs.price ? "invalid" : ""}`} inputMode="decimal" value={f.price} onChange={set("price")} /></Field>
          <Field label={`費用（${inst?.currency ?? "原幣"}）`} error={errs.fee}><input className="input" inputMode="decimal" value={f.fee} onChange={set("fee")} /></Field>
          {Number.isFinite(est) && est > 0 && <p className="small muted">預估{f.type === "sell" ? "成交淨額" : "成本"}：{fmtNum(est)}（以伺服器結果為準）</p>}
        </>}
        <Field label="備註"><input className="input" value={f.note} onChange={set("note")} /></Field>
        {err && <p role="alert" className="err">{err}</p>}
        <div className="row"><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={!inst || busy} onClick={save}>{busy ? "儲存中…" : "儲存"}</button></div>
      </div>
    </Modal>
  );
}

function HistoryModal({ pos, onClose }: { pos: Pos; onClose: () => void }) {
  const { data, reload } = useApi<any[]>(`/positions/${pos.instrument.id}/transactions`);
  const [err, setErr] = useState<string | null>(null);
  async function voidTx(id: string) {
    try { await api(`/positions/transactions/${id}/void`, { method: "POST" }); reload(); } catch (e) { setErr((e as ApiError).message); }
  }
  return (
    <Modal open onClose={onClose} title={`${pos.instrument.name} 交易紀錄`}>
      <table className="tbl small num"><tbody>
        {(data || []).map((t) => (
          <tr key={t.id} style={t.voided ? { textDecoration: "line-through", opacity: .6 } : undefined}>
            <td>{t.date}</td><td>{TX_TYPE[t.type]}</td><td>{t.type === "split" ? `1→${t.split_ratio}` : `${fmtNum(t.qty, 0)} × ${fmtNum(t.price)}`}</td>
            <td>{t.voided ? "已沖銷" : <button className="btn link" onClick={() => voidTx(t.id)}>沖銷</button>}</td>
          </tr>
        ))}
      </tbody></table>
      <p className="tiny muted">修改以沖銷紀錄呈現：沖銷後再新增正確的交易。</p>
      {err && <p className="err" role="alert">{err}</p>}
      <button className="btn" onClick={onClose}>關閉</button>
    </Modal>
  );
}
