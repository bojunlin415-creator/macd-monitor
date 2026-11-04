import { useState } from "react";
import { api, ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ErrorBox, Field, Skeleton, Toast, useApi } from "../components/ui";
import { DATA_STATE, INSTRUMENT_STATE, JOB_STATUS, MARKET, PLAN, ROLE, USER_STATUS, fmtDate } from "../i18n";

export default function Admin() {
  const { me } = useAuth();
  const [tab, setTab] = useState("jobs");
  if (me?.role !== "admin") return <div className="card empty"><p>找不到這個頁面，或你沒有查看權限。</p></div>;
  return (
    <>
      <div className="topbar"><h1>管理中心</h1></div>
      <div className="tabs" role="tablist">
        {[["jobs", "資料作業"], ["members", "成員"], ["license", "授權"], ["audit", "稽核"]].map(([k, l]) => <button key={k} role="tab" className="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      {tab === "jobs" && <Jobs />}
      {tab === "members" && <Members />}
      {tab === "license" && <div className="card"><p>目前使用<strong>示意資料</strong>（虛構代號），未接正式行情供應商。</p><p className="small muted">供應商、授權範圍（展示、非展示、LINE 轉送、匯出）待確認（X-02、X-03）。未取得授權前，匯出功能保持停用。</p></div>}
      {tab === "audit" && <Audit />}
    </>
  );
}

function Jobs() {
  const { data, error, loading, reload } = useApi<any>("/admin/jobs");
  const [market, setMarket] = useState("TW");
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  if (loading && !data) return <Skeleton />;
  if (!data) return error ? <ErrorBox error={error} onRetry={reload} /> : null;
  async function run() {
    setBusy(true);
    try {
      const r = await api<any>("/admin/jobs/run", { method: "POST", body: { market, as_of: date } });
      const j = r.data.job;
      setToast(`${MARKET[market]} ${date}：${JOB_STATUS[j.status]}；新排入通知 ${j.detail?.evaluate?.queued ?? 0} 則（重跑不會重複通知）`); reload();
    } catch (e) { setToast((e as ApiError).message); } finally { setBusy(false); }
  }
  return (
    <div className="stack">
      <div className="card">
        <h2>市場狀態</h2>
        <div className="row">{data.markets.map((m: any) => <span key={m.id} className={`dchip ${m.state}`}><strong>{MARKET[m.id]}</strong>{m.data_cutoff ? `截至 ${fmtDate(m.data_cutoff)}` : "尚無資料"} {DATA_STATE[m.state]}</span>)}</div>
      </div>
      <div className="card stack">
        <h2>手動執行盤後作業</h2>
        <p className="small muted" style={{ margin: 0 }}>範圍：所選市場所有被監測的股票。只評估當前有效週；已發出的通知受去重保護，不會重複推播。</p>
        <div className="row" style={{ alignItems: "flex-end" }}>
          <Field label="市場"><select className="input" value={market} onChange={(e) => setMarket(e.target.value)}>{Object.entries(MARKET).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          <Field label="資料日期"><input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
          <button className="btn primary" disabled={busy} onClick={run}>{busy ? "執行中…" : "執行"}</button>
        </div>
      </div>
      <div className="card">
        <h2>缺資料或準備中的股票</h2>
        {data.problem_instruments.length === 0 ? <p className="muted">沒有。</p> : <table className="tbl small"><tbody>
          {data.problem_instruments.map((i: any) => <tr key={i.id}><td>{MARKET[i.market]} {i.symbol} {i.name}</td><td>{INSTRUMENT_STATE[i.state]}</td></tr>)}
        </tbody></table>}
      </div>
      <div className="card tblwrap">
        <h2>作業時間軸</h2>
        <table className="tbl small"><thead><tr><th>作業</th><th>狀態</th><th>結果</th><th>時間</th></tr></thead><tbody>
          {data.jobs.map((j: any) => (
            <tr key={j.id}><td><code>{j.job_key}</code></td><td>{JOB_STATUS[j.status]}</td>
              <td>{j.error ? <span className="err">{j.error}</span> : j.detail?.evaluate ? `判斷 ${j.detail.evaluate.instruments} 檔，排入通知 ${j.detail.evaluate.queued} 則` : ""}</td>
              <td>{new Date(j.created_at).toLocaleString("zh-TW", { hour12: false })}</td></tr>
          ))}
        </tbody></table>
      </div>
      {toast && <Toast text={toast} onDone={() => setToast(null)} />}
    </div>
  );
}

function Members() {
  const { data, reload } = useApi<any[]>("/admin/members");
  const [email, setEmail] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  async function invite() {
    try { const r = await api<any>("/admin/invitations", { method: "POST", body: { email } }); setMsg(r.data.message + (r.data.dev_link ? `（開發模式連結：${r.data.dev_link}）` : "")); setEmail(""); }
    catch (e) { setMsg((e as ApiError).message); }
  }
  async function setStatus(id: string, status: string) {
    try { await api(`/admin/members/${id}/status`, { method: "PUT", body: { status } }); reload(); } catch (e) { setMsg((e as ApiError).message); }
  }
  async function setPlan(id: string, plan: string) {
    try { await api(`/admin/members/${id}/plan`, { method: "PUT", body: { plan } }); reload(); } catch (e) { setMsg((e as ApiError).message); }
  }
  const pending = (data || []).filter((u) => u.status === "pending").length;
  return (
    <div className="stack">
      <div className="card stack">
        <h2>邀請成員</h2>
        <div className="row"><input className="input" style={{ flex: 1, minWidth: 220 }} type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} aria-label="受邀 Email" /><button className="btn primary" disabled={!email} onClick={invite}>寄出邀請</button></div>
        {msg && <p role="status" className="small" style={{ wordBreak: "break-all" }}>{msg}</p>}
      </div>
      <div className="card tblwrap">
        {pending > 0 && <p className="banner" role="status">有 {pending} 位會員申請等待核准。</p>}
        <table className="tbl cards"><thead><tr><th>Email</th><th>角色</th><th>方案</th><th>狀態</th><th></th></tr></thead><tbody>
          {(data || []).map((u) => (
            <tr key={u.id}><td data-label="Email">{u.email}</td><td data-label="角色">{ROLE[u.role]}</td>
              <td data-label="方案">{u.role === "admin" ? "（不限）" : <select className="input" aria-label={`${u.email} 的方案`} value={u.plan} onChange={(e) => setPlan(u.id, e.target.value)}>{Object.entries(PLAN).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>}</td>
              <td data-label="狀態">{USER_STATUS[u.status]}</td>
              <td>{u.role !== "admin" && <button className="btn link" onClick={() => setStatus(u.id, u.status === "active" ? "disabled" : "active")}>{u.status === "active" ? "停用" : u.status === "pending" ? "核准" : "重新啟用"}</button>}</td></tr>
          ))}
        </tbody></table>
      </div>
    </div>
  );
}

const ACTION: Record<string, string> = { login: "登入", invite: "邀請成員", accept_invite: "接受邀請", consent: "同意告知", line_link: "綁定 LINE", line_unlink: "解除 LINE", job_run: "手動執行作業", member_active: "啟用成員", member_disabled: "停用成員", signup: "會員註冊", member_plan: "調整方案" };
function Audit() {
  const { data } = useApi<any[]>("/admin/audit");
  return (
    <div className="card tblwrap"><table className="tbl small"><thead><tr><th>時間</th><th>動作</th><th>對象</th></tr></thead><tbody>
      {(data || []).map((a) => <tr key={a.id}><td>{new Date(a.at).toLocaleString("zh-TW", { hour12: false })}</td><td>{ACTION[a.action] ?? a.action}</td><td><code>{a.resource}</code></td></tr>)}
    </tbody></table></div>
  );
}
