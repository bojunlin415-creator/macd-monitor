import { Link, useNavigate } from "react-router-dom";
import { DataStatusBar, Empty, ErrorBox, MarketStatus, QuotaBar, SignalBadge, SignalCard, SignalEvent, Skeleton, StatusTag, Change, useApi } from "../components/ui";
import { MARKET, fmtDate, fmtNum } from "../i18n";

interface OverviewData {
  markets: MarketStatus[]; today: SignalEvent[]; today_total: number; holding_risk: SignalEvent[]; holding_count: number;
  week: { watched: number; limit: number; provisional: number; confirmed: number; unable: number; preparing: number };
  events: SignalEvent[]; line_status: string; paused_until: string | null;
}

// 總覽：待處理優先（資料狀態 → 今日待看 → 持股風險 → 本週概況 → 全部事件）
export default function Overview() {
  const { data, error, loading, reload } = useApi<OverviewData>("/overview");
  const nav = useNavigate();
  if (loading && !data) return <Skeleton n={4} />;
  if (!data) return error ? <ErrorBox error={error} onRetry={reload} /> : null;
  const cutoff = data.markets.find((m) => m.data_cutoff);
  const delayed = data.markets.filter((m) => m.state !== "ok");
  return (
    <>
      <div className="topbar"><h1>總覽</h1></div>
      <DataStatusBar markets={data.markets} />
      {error && <ErrorBox error={new Error(`載入失敗，目前顯示先前的資料。${error.message}`)} onRetry={reload} />}
      {delayed.length > 0 && <div className="banner">{delayed.map((m) => MARKET[m.id]).join("、")}資料尚未齊全，部分股票暫時無法判斷。</div>}
      {data.line_status !== "active" && <div className="banner info">尚未綁定 LINE，目前只能在站內查看。<Link to="/settings?tab=notify">前往綁定</Link></div>}
      {data.paused_until && <div className="banner info">LINE 通知暫停至 {fmtDate(data.paused_until)}。</div>}

      <section className="section" aria-labelledby="h-today">
        <div className="topbar"><h2 id="h-today" style={{ margin: 0, flex: 1 }}>今日待看</h2>
          {data.today_total > 6 && <Link to="/signals">查看全部 {data.today_total} 則</Link>}</div>
        {data.week.watched === 0 ? (
          <Empty text="還沒有自選股。加入股票後，我們會在每日盤後幫你檢查。" action={<Link className="btn primary" to="/watchlist">新增股票</Link>} />
        ) : data.today.length === 0 ? (
          <div className="card">本次盤後無新訊號。{cutoff && `${MARKET[cutoff.id]}資料截至 ${fmtDate(cutoff.data_cutoff)} 收盤。`}</div>
        ) : (
          <div className="grid">{data.today.map((e) => <SignalCard key={e.id} e={e} />)}</div>
        )}
      </section>

      <section className="section" aria-labelledby="h-risk">
        <h2 id="h-risk">持股風險</h2>
        {data.holding_risk.length === 0 ? (
          <div className="card">{data.holding_count === 0 ? "尚未記錄持股。" : `持股目前無偏空訊號。${cutoff ? `資料截至 ${fmtDate(cutoff.data_cutoff)}。` : ""}`}</div>
        ) : <div className="grid">{data.holding_risk.map((e) => <SignalCard key={e.id} e={e} />)}</div>}
      </section>

      <section className="section" aria-labelledby="h-week">
        <h2 id="h-week">本週概況</h2>
        <div className="card row" style={{ gap: 32 }}>
          <Link to="/watchlist" style={{ textDecoration: "none", color: "inherit" }}><QuotaBar used={data.week.watched} limit={data.week.limit} /></Link>
          <Link to="/signals?status=provisional">本週暫定 <strong className="num">{data.week.provisional}</strong></Link>
          <Link to="/signals?status=confirmed">已確認 <strong className="num">{data.week.confirmed}</strong></Link>
          <Link to="/watchlist?state=quality_failed">無法判斷 <strong className="num">{data.week.unable}</strong></Link>
          {data.week.preparing > 0 && <Link to="/watchlist">準備中 <strong className="num">{data.week.preparing}</strong></Link>}
        </div>
      </section>

      <section className="section" aria-labelledby="h-all">
        <div className="topbar"><h2 id="h-all" style={{ margin: 0, flex: 1 }}>全部事件</h2><Link to="/signals">查看全部</Link></div>
        {data.events.length === 0 ? <div className="card muted">尚無事件。</div> : (
          <div className="card tblwrap" style={{ padding: 8 }}>
            <table className="tbl cards">
              <thead><tr><th>股票</th><th>等級與方向</th><th>狀態</th><th>原因摘要</th><th>量比</th><th>週變動</th><th>週別</th><th>持股</th></tr></thead>
              <tbody>
                {data.events.map((e) => (
                  <tr key={e.id} className="clickable" onClick={() => nav(`/signals/${e.id}`)} tabIndex={0} onKeyDown={(k) => k.key === "Enter" && nav(`/signals/${e.id}`)}>
                    <td data-label="股票">{e.instrument.name} <span className="muted num">{e.instrument.symbol}</span></td>
                    <td data-label="等級與方向"><SignalBadge code={e.code} level={e.level} direction={e.direction} /></td>
                    <td data-label="狀態"><StatusTag status={e.status} /></td>
                    <td data-label="原因摘要">{e.sentence}</td>
                    <td data-label="量比" className="num">{fmtNum(e.rvol)}</td>
                    <td data-label="週變動" className="num"><Change x={e.week_change} /></td>
                    <td data-label="週別">第 {e.week_no} 週</td>
                    <td data-label="持股">{e.held ? <span className="held">持有</span> : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
