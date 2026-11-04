import { Link } from "react-router-dom";
import { useAuth } from "../lib/auth";

export default function More() {
  const { me } = useAuth();
  const items = [["/backtest", "回測"], ["/settings", "設定"], ...(me?.role === "admin" ? [["/admin", "管理中心"]] : [])];
  return (
    <>
      <h1>更多</h1>
      <div className="card stack" style={{ gap: 0 }}>
        {items.map(([to, l]) => <Link key={to} to={to} className="navlink">{l}</Link>)}
      </div>
    </>
  );
}

export function Backtest() {
  return (
    <>
      <h1>回測</h1>
      <div className="card">
        <p>歷史回測（確認訊號後 1／4／8／12 週的報酬與風險）將於下一階段開放。</p>
        <p className="small muted">回測為事件研究，不是投資組合績效；歷史表現不代表未來報酬。</p>
      </div>
    </>
  );
}

export function DataHelp() {
  return (
    <>
      <h1>資料狀態說明</h1>
      <div className="card stack">
        <p><strong>正常</strong>：該市場最近一次盤後資料已取得並通過檢核。</p>
        <p><strong>延遲</strong>：供應商資料尚未到齊。系統每 15 分鐘重試，2 小時仍不齊會通知管理員；期間受影響股票顯示「無法判斷」，不會推播策略通知。</p>
        <p><strong>異常</strong>：資料檢核失敗（例如價格倒置、單位不明），相關股票已隔離待核實。</p>
        <p className="small muted">單一市場異常不影響其他市場。</p>
      </div>
    </>
  );
}
