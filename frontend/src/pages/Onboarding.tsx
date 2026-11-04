import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { DISCLAIMER, MARKET } from "../i18n";
import { Instrument, QuotaBar } from "../components/ui";
import { LineLinkPanel } from "./Settings";

// 首次使用 4 步引導（規格 9.3）：可返回、LINE 可略過；返回不遺失已選股票
export default function Onboarding() {
  const { me, setMe } = useAuth();
  const nav = useNavigate();
  const [step, setStep] = useState(me?.consent_version ? 2 : 1);
  const [selected, setSelected] = useState<Instrument[]>([]);
  const [used, setUsed] = useState(0);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { api<any>("/watchlist").then((r) => setUsed(r.data.used)).catch(() => {}); }, []);

  async function consent() {
    try { setMe((await api("/me/consent", { method: "POST" })).data); setStep(2); } catch (e) { setErr((e as ApiError).message); }
  }
  async function saveStocks() {
    setErr(null);
    if (selected.length) {
      try {
        await api("/watchlist", { method: "POST", body: { instrument_ids: selected.map((s) => s.id) } });
        setUsed(used + selected.length); setSelected([]);
      } catch (e) { setErr((e as ApiError).message); return; }
    }
    setStep(3);
  }
  async function finish() {
    setMe((await api("/me/onboarding-done", { method: "POST" })).data);
    nav("/", { replace: true });
  }
  return (
    <div style={{ maxWidth: 720, margin: "0 auto", padding: 16 }}>
      <div className="steps" aria-live="polite">第 {step} 步／共 4 步</div>
      <div className="card stack">
        {step === 1 && <>
          <h1>使用前請先閱讀</h1>
          <ul>
            <li>本系統只提供週線技術資訊，<strong>不自動下單</strong>，也不提供買賣股數建議。</li>
            <li>平日盤後的訊號為「本週暫定」，週末可能失效。</li>
            <li>我們只保存你的電子郵件、LINE 綁定與你自行輸入的持股，不會提供給其他成員。</li>
          </ul>
          <details><summary>展開全文</summary><p className="small">{DISCLAIMER}</p></details>
          <div className="row"><button className="btn primary" onClick={consent}>我已閱讀並同意</button></div>
        </>}
        {step === 2 && <>
          <h1>加入第一批股票</h1>
          <QuotaBar used={used + selected.length} limit={200} />
          <StockPicker selected={selected} onChange={setSelected} />
          <p className="small muted">也可以稍後在「自選」頁匯入 Excel 或 CSV。</p>
          {err && <p role="alert" className="err">{err}</p>}
          <div className="row">
            <button className="btn" onClick={() => setStep(1)} disabled={!!me?.consent_version}>上一步</button>
            <button className="btn primary" onClick={saveStocks}>{selected.length ? `加入 ${selected.length} 檔並繼續` : "略過"}</button>
          </div>
        </>}
        {step === 3 && <>
          <h1>確認監測規則</h1>
          <p style={{ fontSize: 20 }}>MACD 12／26／9，首次減弱即通知。</p>
          <details><summary>進階設定</summary><p className="small muted">可在「設定 › 策略設定」調整 MACD 參數與放量門檻，變更於下一交易週生效。</p></details>
          <div className="row"><button className="btn" onClick={() => setStep(2)}>上一步</button><button className="btn primary" onClick={() => setStep(4)}>使用這個規則</button></div>
        </>}
        {step === 4 && <>
          <h1>綁定 LINE</h1>
          <LineLinkPanel />
          <div className="row"><button className="btn" onClick={() => setStep(3)}>上一步</button><button className="btn primary" onClick={finish}>{me?.line.status === "active" ? "完成" : "略過，先在站內查看"}</button></div>
        </>}
      </div>
    </div>
  );
}

export function StockPicker({ selected, onChange }: { selected: Instrument[]; onChange: (s: Instrument[]) => void }) {
  const [q, setQ] = useState("");
  const [market, setMarket] = useState("");
  const [rows, setRows] = useState<(Instrument & { watched: boolean })[]>([]);
  useEffect(() => {
    const t = setTimeout(() => {
      api<any[]>(`/instruments?q=${encodeURIComponent(q)}${market ? `&market=${market}` : ""}`).then((r) => setRows(r.data)).catch(() => setRows([]));
    }, 200);
    return () => clearTimeout(t);
  }, [q, market]);
  const ids = new Set(selected.map((s) => s.id));
  const toggle = (r: Instrument) => onChange(ids.has(r.id) ? selected.filter((s) => s.id !== r.id) : [...selected, r]);
  return (
    <div className="stack">
      <div className="row">
        <input className="input" style={{ flex: 1, minWidth: 200 }} placeholder="搜尋代碼或名稱" value={q} onChange={(e) => setQ(e.target.value)} aria-label="搜尋股票" />
        <select className="input" style={{ width: 120 }} value={market} onChange={(e) => setMarket(e.target.value)} aria-label="市場">
          <option value="">全部市場</option>{Object.entries(MARKET).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </div>
      <div className="tblwrap" style={{ maxHeight: 320, overflowY: "auto" }}>
        <table className="tbl">
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td><label className="row" style={{ cursor: r.watched ? "default" : "pointer" }}>
                  <input type="checkbox" disabled={r.watched} checked={r.watched || ids.has(r.id)} onChange={() => toggle(r)} style={{ width: 20, height: 20 }} />
                  <span>{r.name} <span className="muted num">{r.symbol}</span></span>
                </label></td>
                <td className="muted small">{MARKET[r.market]}</td>
                <td className="muted small">{r.watched ? "已在自選" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="tiny muted">目前為示意資料（虛構代號），正式行情來源確認後替換。</p>
    </div>
  );
}
