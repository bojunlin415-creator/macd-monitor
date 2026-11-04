import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { useSearchParams } from "react-router-dom";
import { api, ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Field, Modal, Toast, useApi } from "../components/ui";
import { OUTBOX_KIND, OUTBOX_STATUS, SIGNAL_NAME, fmtDate, MARKET } from "../i18n";

const TABS = [["rules", "策略設定"], ["notify", "通知設定"], ["appearance", "外觀設定"], ["account", "帳戶"]];

export default function Settings() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "rules";
  return (
    <>
      <div className="topbar"><h1>設定</h1></div>
      <div className="tabs" role="tablist">
        {TABS.map(([k, l]) => <button key={k} role="tab" className="tab" aria-selected={tab === k} onClick={() => setParams({ tab: k }, { replace: true })}>{l}</button>)}
      </div>
      <div className="card" style={{ maxWidth: 760 }}>
        {tab === "rules" && <RuleEditor scope="global" />}
        {tab === "notify" && <NotifySettings />}
        {tab === "appearance" && <Appearance />}
        {tab === "account" && <Account />}
      </div>
    </>
  );
}

// 策略設定：預設只顯示一句話；進階設定展開參數；變更前後對照、生效週、恢復預設
export function RuleEditor({ scope }: { scope: string }) {
  const { data, reload } = useApi<{ rule: any; global: any }>(`/rules/${scope}`);
  const [f, setF] = useState<any>(null);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { if (data) setF({ fast: data.rule.fast, slow: data.rule.slow, signal: data.rule.signal, rvol_up: data.rule.rvol_up }); }, [data]);
  if (!data || !f) return null;
  const r = data.rule;
  const changed = ["fast", "slow", "signal", "rvol_up"].some((k) => Number(f[k]) !== r[k]);
  async function save() {
    setErrs({}); setMsg(null);
    try {
      const res = await api<any>(`/rules/${scope}`, { method: "PUT", body: { fast: Number(f.fast), slow: Number(f.slow), signal: Number(f.signal), rvol_up: Number(f.rvol_up), version: r.version } });
      setMsg(res.data.message); reload();
    } catch (e) { const a = e as ApiError; setErrs(Object.keys(a.fieldErrors).length ? a.fieldErrors : { _: a.message }); }
  }
  async function reset() {
    try { const res = await api<any>(`/rules/${scope}`, { method: "DELETE" }); setMsg(res.data.message); reload(); } catch (e) { setErrs({ _: (e as ApiError).message }); }
  }
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="stack">
      <h2>{scope === "global" ? "預設規則" : "此股規則"} {r.custom && <span className="held">自訂</span>}</h2>
      <p style={{ fontSize: 18, margin: 0 }}>MACD {r.fast}／{r.slow}／{r.signal}，首次減弱即通知。</p>
      {r.effective_week && <p className="small muted" style={{ margin: 0 }}>目前版本自 {fmtDate(r.effective_week)} 當週生效</p>}
      <details>
        <summary>進階設定</summary>
        <div className="stack" style={{ marginTop: 8 }}>
          <div className="row" style={{ alignItems: "flex-start" }}>
            <Field label="快線週數" error={errs.fast}><input className={`input ${errs.fast ? "invalid" : ""}`} style={{ width: 110 }} inputMode="numeric" value={f.fast} onChange={set("fast")} /></Field>
            <Field label="慢線週數" error={errs.slow}><input className={`input ${errs.slow ? "invalid" : ""}`} style={{ width: 110 }} inputMode="numeric" value={f.slow} onChange={set("slow")} /></Field>
            <Field label="訊號線週數" error={errs.signal}><input className={`input ${errs.signal ? "invalid" : ""}`} style={{ width: 110 }} inputMode="numeric" value={f.signal} onChange={set("signal")} /></Field>
            <Field label="放量門檻（量比）" error={errs.rvol_up}><input className={`input ${errs.rvol_up ? "invalid" : ""}`} style={{ width: 130 }} inputMode="decimal" value={f.rvol_up} onChange={set("rvol_up")} /></Field>
          </div>
          {changed && <p className="small">變更前：{r.fast}／{r.slow}／{r.signal}、放量 {r.rvol_up}　→　變更後：{f.fast}／{f.slow}／{f.signal}、放量 {f.rvol_up}（下一交易週生效，本週僅供預覽）</p>}
          {errs._ && <p className="err" role="alert">{errs._}</p>}
          <div className="row">
            <button className="btn primary" disabled={!changed} onClick={save}>儲存</button>
            {scope !== "global" && r.custom && <button className="btn" onClick={reset}>恢復預設</button>}
          </div>
        </div>
      </details>
      {msg && <p role="status" className="banner info">{msg}</p>}
    </div>
  );
}

export function LineLinkPanel() {
  const { me, refresh } = useAuth();
  const [link, setLink] = useState<{ code: string; message_text: string; open_url: string | null; configured: boolean } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!link || me?.line.status === "active") return;
    const t = setInterval(refresh, 4000); // 等待 LINE 端完成綁定
    return () => clearInterval(t);
  }, [link, me?.line.status, refresh]);
  if (me?.line.status === "active") return <p>✓ 已綁定 LINE{me.line.linked_at ? `（${fmtDate(me.line.linked_at)}）` : ""}。綁定時已自動送出測試訊息。</p>;
  async function start() {
    try { setLink((await api<any>("/line/link", { method: "POST" })).data); } catch (e) { setErr((e as ApiError).message); }
  }
  return (
    <div className="stack">
      <p>綁定後，訊號會在盤後即時送到你的 LINE。</p>
      {!link ? <button className="btn primary" style={{ alignSelf: "flex-start" }} onClick={start}>產生綁定碼</button> : (
        <div className="banner info stack" style={{ gap: 8 }}>
          <span>請在 10 分鐘內，把下面這段文字傳給官方帳號：</span>
          <strong style={{ fontSize: 22, letterSpacing: 2 }}>{link.message_text}</strong>
          {link.open_url ? <>
            <a className="btn primary hide-desktop" href={link.open_url}>開啟 LINE</a>
            <QR className="hide-mobile" text={link.open_url} />
          </> : <span className="small muted">LINE 官方帳號尚未設定（管理員需提供 Channel 資訊），目前無法完成綁定。</span>}
          <span className="small muted">綁定完成後此頁會自動更新。</span>
        </div>
      )}
      {err && <p className="err" role="alert">{err}</p>}
    </div>
  );
}

function NotifySettings() {
  const { me, setMe } = useAuth();
  const dl = useApi<any[]>("/line/deliveries");
  const [toast, setToast] = useState<string | null>(null);
  const [confirmUnlink, setConfirmUnlink] = useState(false);
  const [pause, setPause] = useState("");
  if (!me) return null;
  async function save(body: any) {
    try { const r = await api<any>("/me/preferences", { method: "PUT", body }); setMe(r.data); setToast("已儲存。"); }
    catch (e) { setToast((e as ApiError).message); }
  }
  const muted = new Set(me.notify.muted_codes);
  const toggleCode = (c: string) => { const n = new Set(muted); n.has(c) ? n.delete(c) : n.add(c); save({ muted_codes: [...n] }); };
  return (
    <div className="stack">
      <h2>LINE 綁定</h2>
      <LineLinkPanel />
      {me.line.status === "active" && <div className="row">
        <button className="btn" onClick={async () => { try { await api("/line/test", { method: "POST" }); setToast("測試訊息已送出，請查看 LINE。"); dl.reload(); } catch (e) { setToast((e as ApiError).message); } }}>傳送測試訊息</button>
        <button className="btn danger" onClick={() => setConfirmUnlink(true)}>解除綁定</button>
      </div>}

      <h2>依等級訂閱</h2>
      <p className="small muted" style={{ margin: 0 }}>關閉的訊號仍會記錄在站內，只是不推播 LINE。「賣壓／買方首次減弱」依政策不可關閉。</p>
      {["NEG_WEAK", "POS_WEAK", "CROSS", "RISK", "ENTRY", "EXIT"].map((c) => {
        const locked = me.notify.mandatory_codes.includes(c);
        return (
          <label key={c} className="row" style={{ minHeight: 44 }}>
            <input type="checkbox" style={{ width: 20, height: 20 }} checked={!muted.has(c)} disabled={locked} onChange={() => toggleCode(c)} />
            {SIGNAL_NAME[c]}{locked && <span className="small muted">（不可關閉）</span>}
          </label>
        );
      })}

      <h2>合併通知</h2>
      <Field label="同一市場同一批次達幾則時合併為一則清單訊息（3～10），合併不會延後發送">
        <select className="input" style={{ width: 120 }} value={me.notify.merge_threshold} onChange={(e) => save({ merge_threshold: Number(e.target.value) })}>
          {[3, 4, 5, 6, 7, 8, 9, 10].map((n) => <option key={n} value={n}>{n} 則</option>)}
        </select>
      </Field>
      <label className="row"><input type="checkbox" style={{ width: 20, height: 20 }} checked={me.notify.weekly_summary} onChange={(e) => save({ weekly_summary: e.target.checked })} />每週六 12:00 傳送每週摘要</label>

      <h2>夜間通知</h2>
      <p className="small" style={{ margin: 0 }}>系統不提供勿擾時段，訊號一律即時發送。美股收盤約為台北時間清晨 4–5 點，如需安靜，請使用手機的勿擾模式，訊息仍會即時送達。</p>

      <h2>暫停通知</h2>
      {me.notify.paused_until ? <div className="row"><span>LINE 通知暫停至 {fmtDate(me.notify.paused_until)}</span><button className="btn" onClick={() => save({ clear_pause: true })}>恢復通知</button></div> :
        <div className="row"><input className="input" type="date" style={{ width: 180 }} value={pause} onChange={(e) => setPause(e.target.value)} aria-label="暫停至" /><button className="btn" disabled={!pause} onClick={() => save({ paused_until: pause })}>暫停至此日</button></div>}

      <h2>最近投遞紀錄</h2>
      {(dl.data || []).length === 0 ? <p className="muted small">尚無紀錄。</p> : (
        <table className="tbl small"><tbody>
          {dl.data!.map((d) => <tr key={d.id}><td>{fmtDate(d.created_at)}</td><td>{OUTBOX_KIND[d.kind]}</td><td>{d.symbol ? `${MARKET[d.market]} ${d.symbol} ${d.name}` : ""}</td><td>{OUTBOX_STATUS[d.status]}</td></tr>)}
        </tbody></table>
      )}
      <Modal open={confirmUnlink} onClose={() => setConfirmUnlink(false)} title="解除 LINE 綁定">
        <p>解除後將立即停止所有 LINE 推播，尚未送出的訊息也會取消。確定要解除嗎？</p>
        <div className="row"><button className="btn" onClick={() => setConfirmUnlink(false)}>取消</button>
          <button className="btn danger" onClick={async () => { await api("/line/link", { method: "DELETE" }); setConfirmUnlink(false); const r = await api<any>("/me"); setMe(r.data); setToast("已解除綁定。"); }}>解除綁定</button></div>
      </Modal>
      {toast && <Toast text={toast} onDone={() => setToast(null)} />}
    </div>
  );
}

function Appearance() {
  const { me, setMe } = useAuth();
  if (!me) return null;
  async function save(body: any) { setMe((await api<any>("/me/preferences", { method: "PUT", body })).data); }
  const Radio = ({ name, value, label, cur }: { name: string; value: string; label: string; cur: string }) => (
    <label className="row" style={{ minHeight: 44 }}><input type="radio" name={name} checked={cur === value} onChange={() => save({ [name]: value })} style={{ width: 20, height: 20 }} />{label}</label>
  );
  return (
    <div className="stack">
      <fieldset style={{ border: "none", padding: 0 }}><legend><h2>主題</h2></legend>
        <Radio name="theme" value="system" label="跟隨系統" cur={me.theme} /><Radio name="theme" value="light" label="淺色" cur={me.theme} /><Radio name="theme" value="dark" label="深色" cur={me.theme} />
      </fieldset>
      <fieldset style={{ border: "none", padding: 0 }}><legend><h2>漲跌色</h2></legend>
        <Radio name="updown_color" value="red_up" label="紅漲綠跌（台灣慣例）" cur={me.updown_color} /><Radio name="updown_color" value="green_up" label="綠漲紅跌" cur={me.updown_color} />
        <p className="small muted">漲跌色只用在價格與 K 棒；訊號等級色不會隨之改變。</p>
      </fieldset>
      <fieldset style={{ border: "none", padding: 0 }}><legend><h2>表格密度</h2></legend>
        <Radio name="table_density" value="normal" label="標準" cur={me.table_density} /><Radio name="table_density" value="compact" label="緊密" cur={me.table_density} />
      </fieldset>
    </div>
  );
}

function Account() {
  const { me, setMe } = useAuth();
  const [name, setName] = useState(me?.display_name || "");
  const [msg, setMsg] = useState<string | null>(null);
  if (!me) return null;
  return (
    <div className="stack">
      <h2>帳戶</h2>
      <p>{me.email}（{me.role === "admin" ? "管理員" : "成員"}）</p>
      <Field label="顯示名稱"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <div className="row">
        <button className="btn primary" onClick={async () => { setMe((await api<any>("/me/preferences", { method: "PUT", body: { display_name: name } })).data); setMsg("已儲存。"); }}>儲存</button>
        <button className="btn" onClick={async () => { await api("/auth/logout", { method: "POST" }); setMe(null); location.href = "/login"; }}>登出</button>
      </div>
      {msg && <p role="status">{msg}</p>}
    </div>
  );
}

function QR({ text, className }: { text: string; className?: string }) {
  const [src, setSrc] = useState<string>("");
  useEffect(() => { QRCode.toDataURL(text, { width: 180, margin: 1 }).then(setSrc).catch(() => setSrc("")); }, [text]);
  return src ? <img className={className} width={180} height={180} alt="用手機掃描以開啟 LINE" src={src} /> : null;
}
