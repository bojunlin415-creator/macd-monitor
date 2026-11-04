import { Link } from "react-router-dom";
import { ErrorBox, QuotaBar, Skeleton, useApi } from "../components/ui";
import { MARKET, fmtDate } from "../i18n";

interface PlanInfo {
  id: string; name: string; watch_limit: number; markets: string[]; line_merge_max: number;
  price_twd_month: number | null; purchasable: boolean; features: string[];
}
interface MembershipData {
  plan: PlanInfo; current_period_end: string | null; usage: { watchlist: number };
  plans: PlanInfo[]; payments_enabled: boolean;
}

// 我的方案：目前方案、用量與方案比較。付費功能開放前，升級按鈕顯示「即將推出」。
export default function Membership() {
  const { data, error, loading, reload } = useApi<MembershipData>("/me/membership");
  if (loading && !data) return <Skeleton />;
  if (!data) return error ? <ErrorBox error={error} onRetry={reload} /> : null;
  const { plan, usage, plans } = data;
  return (
    <>
      <div className="topbar"><h1>我的方案</h1></div>
      <div className="stack" style={{ maxWidth: 760 }}>
        <div className="card stack">
          <h2 style={{ margin: 0 }}>目前方案：{plan.name}</h2>
          {data.current_period_end && <p className="small muted" style={{ margin: 0 }}>有效至 {fmtDate(data.current_period_end.slice(0, 10))}，到期後自動回到免費會員。</p>}
          <div>
            <p className="small" style={{ margin: "0 0 4px" }}>自選股</p>
            <QuotaBar used={usage.watchlist} limit={plan.watch_limit} />
          </div>
          <p className="small" style={{ margin: 0 }}>可監測市場：{plan.markets.map((m) => MARKET[m] ?? m).join("、")}</p>
          <p className="small" style={{ margin: 0 }}>LINE 推播：免費會員也可綁定，<Link to="/settings?tab=notify">前往通知設定</Link>。</p>
        </div>
        <div className="card">
          <h2>方案比較</h2>
          <div className="row" style={{ alignItems: "stretch" }}>
            {plans.map((p) => (
              <div key={p.id} className="card stack" style={{ flex: "1 1 240px", margin: 0, borderColor: p.id === plan.id ? "var(--accent, currentColor)" : undefined }}>
                <h3 style={{ margin: 0 }}>{p.name}</h3>
                <p style={{ margin: 0 }}><strong>{p.price_twd_month == null ? (p.id === "free" ? "免費" : "價格未定") : `每月 NT$${p.price_twd_month}`}</strong></p>
                <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{p.features.map((f) => <li key={f}>{f}</li>)}</ul>
                {p.id === plan.id ? <span className="small muted">目前方案</span>
                  : p.purchasable ? <button className="btn primary">升級</button>
                    : p.id !== "free" && <button className="btn" disabled>即將推出</button>}
              </div>
            ))}
          </div>
          {!data.payments_enabled && <p className="small muted">線上付款尚未開放。若需要更高額度，請聯絡管理員。</p>}
        </div>
      </div>
    </>
  );
}
