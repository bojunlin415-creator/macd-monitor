import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { createChart, IChartApi, ISeriesApi, LineStyle, Time } from "lightweight-charts";
import { api, ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Change, ErrorBox, Instrument, SignalBadge, Skeleton, StatusTag, useApi } from "../components/ui";
import { INSTRUMENT_STATE, MARKET, WEEK_STATUS, fmtDate, fmtNum, weekTitle } from "../i18n";
import { RuleEditor } from "./Settings";

interface Week { week_key: string; status: string; o: number; h: number; l: number; c: number; v: number; elapsed: number; expected: number; dif: number | null; dea: number | null; hist: number | null; ma5: number | null; ma10: number | null; ma20: number | null; ma60: number | null; rvol: number | null }
interface Marker { id: string; week_key: string; code: string; level: string; direction: string; status: string }
interface WeeklyData { weeks: Week[]; markers: Marker[]; params: number[]; state: string; weeks_available: number; warmup_weeks: number; basis: string; raw_last_close: number | null }

function cssVar(name: string) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

export default function InstrumentPage() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "signals";
  const [basis, setBasis] = useState("adjusted");
  const head = useApi<Instrument & { watch_id: string | null; market_cutoff: string }>(`/instruments/${id}`);
  const wk = useApi<WeeklyData>(`/instruments/${id}/weekly?weeks=156&basis=${basis}`);
  const inst = head.data;
  const weeks = wk.data?.weeks || [];
  const last = weeks[weeks.length - 1];
  const prev = weeks[weeks.length - 2];
  const [busy, setBusy] = useState(false);

  async function toggleWatch() {
    if (!inst || busy) return;
    setBusy(true);
    try {
      if (inst.watch_id) await api(`/watchlist/${inst.watch_id}`, { method: "DELETE" });
      else await api("/watchlist", { method: "POST", body: { instrument_ids: [inst.id] } });
      head.reload();
    } catch (e) { alert((e as ApiError).message); } finally { setBusy(false); }
  }

  if (head.loading && !inst) return <Skeleton n={3} />;
  if (!inst) return head.error ? <ErrorBox error={head.error} onRetry={head.reload} /> : null;
  return (
    <div className="stack">
      <section className="card stack" style={{ gap: 8 }}>
        <div className="row">
          <h1 style={{ margin: 0, flex: 1 }}>{inst.name} <span className="muted num">{inst.symbol}</span></h1>
          <label className="row small"><input type="checkbox" checked={!!inst.watch_id} onChange={toggleWatch} disabled={busy} style={{ width: 20, height: 20 }} />監測中</label>
        </div>
        <div className="row small muted">
          <span>{MARKET[inst.market]}・{inst.mic}・{inst.currency}</span>
          <span>資料截至 {fmtDate(inst.market_cutoff)} 收盤</span>
          {inst.license_status === "sample" && <span>示意資料，非真實行情</span>}
          {inst.license_status === "infoway" && <span>資料來源：Infoway（未還原權值）</span>}
        </div>
        {last && (
          <div className="row">
            <span style={{ fontSize: 24, fontWeight: 700 }} className="num">{fmtNum(wk.data?.raw_last_close)}</span>
            <span>週變動 <Change x={prev ? last.c / prev.c - 1 : null} /></span>
            <span className={`status ${last.status}`}>{last.status === "provisional" ? `本週暫定：已完成 ${last.elapsed}／${last.expected} 時段` : WEEK_STATUS[last.status]}</span>
          </div>
        )}
        {wk.data && wk.data.state !== "ready" && (
          <div className="banner info">{wk.data.state === "warmup" ? `暖機中：已累積 ${wk.data.weeks_available}／${wk.data.warmup_weeks} 週` : INSTRUMENT_STATE[wk.data.state]}</div>
        )}
      </section>

      <section className="card">
        <div className="row" style={{ marginBottom: 8 }}>
          <h2 style={{ margin: 0, flex: 1 }}>週線圖</h2>
          <div className="chipgroup" role="group" aria-label="價格基礎">
            <button className="chip" aria-pressed={basis === "adjusted"} onClick={() => setBasis("adjusted")}>調整後</button>
            <button className="chip" aria-pressed={basis === "raw"} onClick={() => setBasis("raw")}>原價</button>
          </div>
        </div>
        {wk.loading && !wk.data ? <Skeleton n={1} /> : wk.error && !wk.data ? <ErrorBox error={wk.error} onRetry={wk.reload} /> :
          weeks.length === 0 ? <p className="muted">準備中：補抓歷史資料</p> : <WeeklyChart data={wk.data!} />}
      </section>

      <div className="tabs" role="tablist">
        {[["signals", "訊號歷史"], ["rules", "規則"], ["position", "持股"], ["table", "數值表"]].map(([k, l]) => (
          <button key={k} role="tab" className="tab" aria-selected={tab === k} onClick={() => setParams({ tab: k }, { replace: true })}>{l}</button>
        ))}
      </div>
      {tab === "signals" && <SignalHistory markers={wk.data?.markers || []} />}
      {tab === "rules" && <div className="card"><RuleEditor scope={inst.id} /></div>}
      {tab === "position" && <div className="card"><p>持股與交易紀錄請至 <Link to={`/positions?instrument=${inst.id}`}>持股頁</Link> 管理。</p></div>}
      {tab === "table" && <ValueTable weeks={weeks} />}
    </div>
  );
}

function SignalHistory({ markers }: { markers: Marker[] }) {
  if (!markers.length) return <div className="card muted">這檔股票尚無訊號。</div>;
  return (
    <div className="card stack" style={{ gap: 0 }}>
      {[...markers].sort((a, b) => b.week_key.localeCompare(a.week_key)).map((m) => (
        <Link key={m.id} to={`/signals/${m.id}`} className="row" style={{ padding: "10px 0", borderBottom: "1px solid var(--line)", color: "inherit", textDecoration: "none" }}>
          <span className="small muted" style={{ width: 220 }}>{weekTitle(m.week_key)}</span>
          <SignalBadge code={m.code} level={m.level} direction={m.direction} /><StatusTag status={m.status} />
        </Link>
      ))}
    </div>
  );
}

function ValueTable({ weeks }: { weeks: Week[] }) {
  return (
    <div className="card tblwrap" style={{ maxHeight: 480, overflowY: "auto" }}>
      <table className="tbl num">
        <thead><tr><th>週別</th><th>狀態</th><th>收盤</th><th>MA20</th><th>快線（DIF）</th><th>慢線（DEA）</th><th>柱體</th><th>量比</th></tr></thead>
        <tbody>
          {[...weeks].reverse().map((w) => (
            <tr key={w.week_key}><td>{weekTitle(w.week_key)}</td><td>{WEEK_STATUS[w.status]}</td><td>{fmtNum(w.c)}</td><td>{fmtNum(w.ma20)}</td>
              <td>{fmtNum(w.dif, 4)}</td><td>{fmtNum(w.dea, 4)}</td><td>{fmtNum(w.hist, 4)}</td><td>{fmtNum(w.rvol)}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const LEVEL_VAR: Record<string, string> = { observe: "--observe", attention: "--attention", warning: "--warning", candidate: "--candidate" };

// 週 K＋均線、成交量、MACD 三區，時間軸與游標同步；鍵盤左右鍵逐週移動
function WeeklyChart({ data }: { data: WeeklyData }) {
  const nav = useNavigate();
  const { me } = useAuth();
  const refs = [useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null)];
  const [idx, setIdx] = useState(data.weeks.length - 1);
  const charts = useRef<IChartApi[]>([]);
  const series = useRef<ISeriesApi<any>[]>([]);
  const weeks = data.weeks;
  const t = (w: Week) => w.week_key as Time;

  useEffect(() => {
    const text = cssVar("--text-2"), line = cssVar("--line"), rise = cssVar("--rise"), fall = cssVar("--fall");
    const base = { layout: { background: { color: "transparent" }, textColor: text, fontFamily: "Noto Sans TC, sans-serif" },
      grid: { vertLines: { color: line }, horzLines: { color: line } }, rightPriceScale: { borderColor: line },
      timeScale: { borderColor: line }, autoSize: true,
      localization: { locale: "zh-TW", timeFormatter: (tm: any) => (typeof tm === "string" ? tm.replace(/-/g, "/") : `${tm.year}/${tm.month}/${tm.day}`) } };
    const heights = [320, 110, 170];
    const cs = refs.map((r, i) => createChart(r.current!, { ...base, height: heights[i], timeScale: { ...base.timeScale, visible: i === 2 } }));
    charts.current = cs;
    const last = weeks.length - 1;
    const candle = cs[0].addCandlestickSeries({ upColor: rise, downColor: fall, borderUpColor: rise, borderDownColor: fall, wickUpColor: rise, wickDownColor: fall });
    candle.setData(weeks.map((w, i) => {
      const up = w.c >= w.o;
      const col = up ? rise : fall;
      // 暫定 K 棒以空心呈現
      return i === last && w.status !== "confirmed"
        ? { time: t(w), open: w.o, high: w.h, low: w.l, close: w.c, color: "transparent", borderColor: col, wickColor: col }
        : { time: t(w), open: w.o, high: w.h, low: w.l, close: w.c };
    }));
    const maColors = ["#8A6D3B", "#00737A", "#4338CA", "#A1176B"];
    (["ma5", "ma10", "ma20", "ma60"] as const).forEach((k, i) => {
      const s = cs[0].addLineSeries({ color: maColors[i], lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(weeks.filter((w) => w[k] !== null).map((w) => ({ time: t(w), value: w[k]! })));
    });
    const vol = cs[1].addHistogramSeries({ priceFormat: { type: "volume" }, priceLineVisible: false });
    vol.setData(weeks.map((w) => ({ time: t(w), value: w.v, color: w.c >= w.o ? rise : fall })));
    const hist = cs[2].addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
    hist.setData(weeks.filter((w) => w.hist !== null).map((w) => ({ time: t(w), value: w.hist!, color: w.hist! >= 0 ? rise : fall })));
    const dif = cs[2].addLineSeries({ color: cssVar("--observe"), lineWidth: 2, priceLineVisible: false });
    dif.setData(weeks.filter((w) => w.dif !== null).map((w) => ({ time: t(w), value: w.dif! })));
    const dea = cs[2].addLineSeries({ color: cssVar("--attention"), lineWidth: 2, lineStyle: LineStyle.Dashed, priceLineVisible: false });
    dea.setData(weeks.filter((w) => w.dea !== null).map((w) => ({ time: t(w), value: w.dea! })));
    const inRange = new Set(weeks.map((w) => w.week_key));
    candle.setMarkers(data.markers.filter((m) => inRange.has(m.week_key)).sort((a, b) => a.week_key.localeCompare(b.week_key)).map((m) => ({
      time: m.week_key as Time, position: m.direction === "bullish" ? "belowBar" : "aboveBar",
      shape: m.direction === "bullish" ? "arrowUp" : "arrowDown", color: cssVar(LEVEL_VAR[m.level]),
      text: { observe: "觀", attention: "注", warning: "警", candidate: "候" }[m.level] || "", id: m.id,
    })));
    series.current = [candle, vol, hist];
    // 同步時間軸
    let syncing = false;
    cs.forEach((c) => c.timeScale().subscribeVisibleLogicalRangeChange((r) => {
      if (syncing || !r) return;
      syncing = true; cs.forEach((o) => o !== c && o.timeScale().setVisibleLogicalRange(r)); syncing = false;
    }));
    // 同步游標
    cs.forEach((c, ci) => c.subscribeCrosshairMove((p) => {
      if (!p.time) return;
      const i = weeks.findIndex((w) => w.week_key === p.time);
      if (i >= 0) setIdx(i);
      cs.forEach((o, oi) => { if (oi !== ci) o.setCrosshairPosition(NaN, p.time!, series.current[oi]); });
    }));
    cs[0].subscribeClick((p) => {
      const m = data.markers.find((x) => x.week_key === p.time);
      if (m) nav(`/signals/${m.id}`);
    });
    cs[0].timeScale().setVisibleLogicalRange({ from: Math.max(0, weeks.length - (window.innerWidth < 768 ? 52 : 104)), to: weeks.length + 2 });
    return () => cs.forEach((c) => c.remove());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, me?.theme, me?.updown_color]);

  const cur = weeks[idx];
  const announce = useMemo(() => cur ? `${weekTitle(cur.week_key).replace(/（.*）/, "")}，收盤 ${fmtNum(cur.c)}，柱體 ${fmtNum(cur.hist)}` : "", [cur]);
  function onKey(e: React.KeyboardEvent) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const n = Math.max(0, Math.min(weeks.length - 1, idx + (e.key === "ArrowLeft" ? -1 : 1)));
    setIdx(n);
    charts.current.forEach((c, ci) => c.setCrosshairPosition(NaN, weeks[n].week_key as Time, series.current[ci]));
  }
  return (
    <div className="chartbox" tabIndex={0} onKeyDown={onKey} aria-label="週線圖，可用左右鍵逐週移動" role="application">
      <div className="legend">
        <span><i style={{ background: "#8A6D3B" }} />MA5</span><span><i style={{ background: "#00737A" }} />MA10</span>
        <span><i style={{ background: "#4338CA" }} />MA20</span><span><i style={{ background: "#A1176B" }} />MA60</span>
        <span>{data.basis === "raw" ? "K 棒為原價；均線與 MACD 仍為調整基礎" : "調整後價格"}</span>
        <span>最後一根空心 K 棒為本週暫定</span>
      </div>
      <div ref={refs[0]} />
      <div className="legend"><span>成交量（股）</span></div>
      <div ref={refs[1]} />
      <div className="legend">
        <span>MACD {data.params.join("／")}</span>
        <span><i style={{ background: "var(--observe)" }} />快線（DIF）</span><span><i style={{ background: "var(--attention)" }} />慢線（DEA）</span><span>柱體</span>
      </div>
      <div ref={refs[2]} />
      <p className="small num" aria-live="polite" style={{ margin: "8px 0 0" }}>
        {cur && <>{announce}；DIF {fmtNum(cur.dif, 4)}、DEA {fmtNum(cur.dea, 4)}、量比 {fmtNum(cur.rvol)}</>}
      </p>
    </div>
  );
}
