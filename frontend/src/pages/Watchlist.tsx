import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../lib/api";
import { Empty, ErrorBox, Instrument, Modal, QuotaBar, Skeleton, Toast, useApi } from "../components/ui";
import { INSTRUMENT_STATE, MARKET } from "../i18n";
import { StockPicker } from "./Onboarding";

interface WL { items: { id: string; instrument: Instrument; group: string | null }[]; used: number; limit: number; groups: { id: string; name: string }[] }

export default function Watchlist() {
  const { data, error, loading, reload } = useApi<WL>("/watchlist");
  const [params] = useSearchParams();
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [confirmRm, setConfirmRm] = useState<{ id: string; name: string } | null>(null);
  const stateFilter = params.get("state");
  const [group, setGroup] = useState("");
  if (loading && !data) return <Skeleton />;
  if (!data) return error ? <ErrorBox error={error} onRetry={reload} /> : null;
  const items = data.items.filter((w) => (!stateFilter || w.instrument.state === stateFilter) && (!group || w.group === group));
  async function remove(id: string) {
    try { await api(`/watchlist/${id}`, { method: "DELETE" }); setToast("已移除。已發出的訊號與持股不受影響。"); reload(); }
    catch (e) { setToast((e as ApiError).message); }
    setConfirmRm(null);
  }
  const full = data.used >= data.limit;
  return (
    <>
      <div className="topbar">
        <h1>自選</h1>
        <QuotaBar used={data.used} limit={data.limit} />
        <button className="btn primary" disabled={full} onClick={() => setAdding(true)}>新增股票</button>
        <button className="btn" disabled={full} onClick={() => setImporting(true)}>從 CSV 匯入</button>
      </div>
      {data.groups.length > 0 && (
        <div className="chipgroup" style={{ marginBottom: 16 }}>
          <span>分組</span>
          <button className="chip" aria-pressed={!group} onClick={() => setGroup("")}>全部</button>
          {data.groups.map((g) => <button key={g.id} className="chip" aria-pressed={group === g.name} onClick={() => setGroup(g.name)}>{g.name}</button>)}
        </div>
      )}
      {stateFilter && <div className="banner info">只顯示「{INSTRUMENT_STATE[stateFilter]}」的股票。<Link to="/watchlist">顯示全部</Link></div>}
      {items.length === 0 ? <Empty text="還沒有自選股。加入股票後，我們會在每日盤後幫你檢查。" action={<button className="btn primary" onClick={() => setAdding(true)}>新增股票</button>} /> : (
        <div className="card tblwrap" style={{ padding: 8 }}>
          <table className="tbl cards">
            <thead><tr><th>股票</th><th>市場</th><th>分組</th><th>監測狀態</th><th></th></tr></thead>
            <tbody>
              {items.map((w) => (
                <tr key={w.id}>
                  <td data-label="股票"><Link to={`/instruments/${w.instrument.id}`}>{w.instrument.name}</Link> <span className="muted num">{w.instrument.symbol}</span></td>
                  <td data-label="市場">{MARKET[w.instrument.market]}</td>
                  <td data-label="分組">{w.group || <span className="muted">未分組</span>}</td>
                  <td data-label="監測狀態">{w.instrument.state === "warmup" ? `暖機中（已累積 ${w.instrument.weeks_available}／139 週）` : INSTRUMENT_STATE[w.instrument.state]}</td>
                  <td><button className="btn link" onClick={() => setConfirmRm({ id: w.id, name: w.instrument.name })}>移除</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <AddModal open={adding} onClose={() => setAdding(false)} used={data.used} limit={data.limit} groups={data.groups.map((g) => g.name)} onDone={(n) => { setToast(`已加入 ${n} 檔。新股票本週先建立狀態，下一個新事件才通知。`); reload(); }} />
      <ImportModal open={importing} onClose={() => setImporting(false)} onDone={(n) => { setToast(`已匯入 ${n} 檔。`); reload(); }} />
      <Modal open={!!confirmRm} onClose={() => setConfirmRm(null)} title="移除自選">
        <p>確定要停止監測「{confirmRm?.name}」嗎？已發出的訊號與持股紀錄會保留。</p>
        <div className="row"><button className="btn" onClick={() => setConfirmRm(null)}>取消</button><button className="btn danger" onClick={() => confirmRm && remove(confirmRm.id)}>移除</button></div>
      </Modal>
      {toast && <Toast text={toast} onDone={() => setToast(null)} />}
    </>
  );
}

function AddModal({ open, onClose, used, limit, groups, onDone }: { open: boolean; onClose: () => void; used: number; limit: number; groups: string[]; onDone: (n: number) => void }) {
  const [sel, setSel] = useState<Instrument[]>([]);
  const [group, setGroup] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true); setErr(null);
    try {
      const r = await api<{ added: number }>("/watchlist", { method: "POST", body: { instrument_ids: sel.map((s) => s.id), group: group || null } });
      onDone(r.data.added); setSel([]); onClose();
    } catch (e) { setErr((e as ApiError).message); } finally { setBusy(false); }
  }
  return (
    <Modal open={open} onClose={onClose} title="新增股票">
      <div className="stack">
        <span className="small">已選 {sel.length}，加入後 {used + sel.length}／{limit}</span>
        <StockPicker selected={sel} onChange={setSel} />
        <input className="input" list="groups" placeholder="分組（選填）" value={group} onChange={(e) => setGroup(e.target.value)} aria-label="分組" />
        <datalist id="groups">{groups.map((g) => <option key={g} value={g} />)}</datalist>
        {err && <p role="alert" className="err">{err}</p>}
        <div className="row"><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={!sel.length || busy} onClick={save}>加入 {sel.length} 檔</button></div>
      </div>
    </Modal>
  );
}

interface PreviewRow { line: number; market: string; symbol: string; group: string | null; kind: string; reason?: string; name?: string }
function ImportModal({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: (n: number) => void }) {
  const [preview, setPreview] = useState<{ preview_id: string; rows: PreviewRow[]; used: number; limit: number } | null>(null);
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [tab, setTab] = useState("ok");
  const [err, setErr] = useState<string | null>(null);
  async function upload(f: File) {
    setErr(null);
    const fd = new FormData(); fd.append("file", f);
    try {
      const r = await api<any>("/watchlist/import-preview", { method: "POST", form: fd });
      setPreview(r.data); setChecked(new Set(r.data.rows.filter((x: PreviewRow) => x.kind === "ok").map((x: PreviewRow) => x.line)));
    } catch (e) { setErr((e as ApiError).message); }
  }
  async function commit() {
    if (!preview) return;
    try {
      const r = await api<{ added: number }>("/watchlist/import-commit", { method: "POST", body: { preview_id: preview.preview_id, selected_lines: [...checked] } });
      onDone(r.data.added); setPreview(null); onClose();
    } catch (e) { setErr((e as ApiError).message); }
  }
  const over = preview ? preview.used + checked.size - preview.limit : 0;
  const tabs = [["ok", "可加入"], ["duplicate", "重複"], ["error", "錯誤"]];
  return (
    <Modal open={open} onClose={onClose} title="從 CSV 匯入">
      <div className="stack">
        <p className="small muted">欄位：market, symbol, group, fast, slow, signal（UTF-8，上限 1 MB／1,000 列）。market 為 TW、US 或 JP。</p>
        <a className="small" href={"data:text/csv;charset=utf-8,%EF%BB%BFmarket,symbol,group,fast,slow,signal%0ATW,T001,半導體,,,%0A"} download="自選匯入範本.csv">下載範本</a>
        <input type="file" accept=".csv,text/csv" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} aria-label="選擇 CSV 檔" />
        {preview && <>
          <div className="tabs" role="tablist">{tabs.map(([k, l]) => <button key={k} role="tab" className="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{l}（{preview.rows.filter((r) => r.kind === k).length}）</button>)}</div>
          <div style={{ maxHeight: 260, overflowY: "auto" }}>
            <table className="tbl small"><tbody>
              {preview.rows.filter((r) => r.kind === tab).map((r) => (
                <tr key={r.line}>
                  <td>{tab === "ok" && <input type="checkbox" checked={checked.has(r.line)} onChange={() => { const n = new Set(checked); n.has(r.line) ? n.delete(r.line) : n.add(r.line); setChecked(n); }} aria-label={`第 ${r.line} 列`} />}</td>
                  <td>第 {r.line} 列</td><td>{MARKET[r.market] ?? r.market} {r.symbol} {r.name ?? ""}</td><td className="muted">{r.reason ?? r.group ?? ""}</td>
                </tr>
              ))}
            </tbody></table>
          </div>
          {over > 0 && <p role="alert" className="err">超過 200 檔上限，請再取消勾選 {over} 檔。</p>}
        </>}
        {err && <p role="alert" className="err">{err}</p>}
        <div className="row"><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={!preview || checked.size === 0 || over > 0} onClick={commit}>提交 {checked.size} 檔</button></div>
      </div>
    </Modal>
  );
}
