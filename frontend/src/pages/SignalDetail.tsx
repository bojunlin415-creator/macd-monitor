import { Link, useParams } from "react-router-dom";
import { ConditionRow, ErrorBox, RiskNote, SignalBadge, SignalEvent, Skeleton, StatusTag, Change, useApi } from "../components/ui";
import { CONDITION, EVENT_STATUS, MARKET, exch, SIGNAL_NAME, WEAKEN_NOTE, fmtDate, fmtNum, weekTitle } from "../i18n";

interface Detail extends SignalEvent {
  conditions: { key: string; state: string; actual: number | null; threshold: number | null }[];
  eps: number; snapshot: any; upgrade_hint: { target: string; missing: string[]; any: boolean } | null;
  versions: { engine: string; input_hash: string; rule_version: number; data_revision: number };
  history: { from: string | null; to: string; at: string }[];
}

const CONCLUSION: Record<string, string> = {
  provisional: "本週尚未結束，週末可能失效。",
  provisional_inactive: "本週目前不成立，等待週末結論。",
  confirmed: "本週收盤後條件仍成立。",
  invalidated: "本週收盤後條件不再成立。",
  superseded: "資料更正後，原結論已被更新。",
};

const REL: Record<string, string[]> = {
  POS_WEAK: ["pos_shrink", "volume_down_price", "break_ma20"],
  RISK: ["pos_shrink", "weak_background", "volume_down_price", "break_ma20"],
  EXIT: ["death_cross", "volume_down_price", "break_ma20"],
  NEG_WEAK: ["neg_shrink", "golden_cross", "above_ma20", "price_up", "volume_up"],
  CROSS: ["golden_cross", "above_ma20", "price_up", "volume_up"],
  ENTRY: ["golden_cross", "above_ma20", "price_up", "volume_up"],
};

// 三層漸進揭露：結論 → 條件檢核 → 數值與稽核
export default function SignalDetail() {
  const { id } = useParams();
  const { data: d, error, loading, reload } = useApi<Detail>(`/signals/${id}`);
  if (loading && !d) return <Skeleton n={3} />;
  if (!d) return error ? <ErrorBox error={error} onRetry={reload} /> : null;
  const s = d.snapshot || {};
  const rel = REL[d.code] || [];
  const relevant = d.conditions.filter((c) => rel.includes(c.key));
  return (
    <div className="stack" style={{ maxWidth: 880 }}>
      <Link to="/signals" className="small">← 返回訊號</Link>
      <section className="card stack" aria-label="結論">
        <div className="row"><SignalBadge code={d.code} level={d.level} direction={d.direction} /><StatusTag status={d.status} />{d.held && <span className="held">持有</span>}</div>
        <h1 style={{ margin: 0 }}>{d.instrument.name} <span className="muted num">{d.instrument.symbol}</span> <span className="muted small">{MARKET[d.instrument.market]}・{exch(d.instrument.mic)}</span></h1>
        <p style={{ fontSize: 18, margin: 0 }}>{d.sentence}。{CONCLUSION[d.status]}</p>
        {d.also.length > 0 && <p className="small muted" style={{ margin: 0 }}>也符合：{d.also.map((c) => SIGNAL_NAME[c]).join("、")}</p>}
        <div className="row small muted">
          <span>{weekTitle(d.week_key)}</span>
          {d.status === "provisional" && s.expected ? <span>本週已完成 {s.elapsed}／{s.expected} 時段</span> : null}
          <span>收盤 {fmtNum(d.close)}（週 <Change x={d.week_change} />）</span>
        </div>
        <RiskNote text={`${WEAKEN_NOTE}本資訊為週線技術分析，不構成投資建議。`} />
      </section>

      <section className="card" aria-labelledby="h-cond">
        <h2 id="h-cond">條件檢核</h2>
        {relevant.map((c) => <ConditionRow key={c.key} c={c} />)}
        {d.upgrade_hint && (
          <p className="small" style={{ marginTop: 12 }}>
            若要升級為「{d.upgrade_hint.target === "RISK" ? "警示" : SIGNAL_NAME[d.upgrade_hint.target]}」，
            {d.upgrade_hint.missing.length === 0 ? "條件已具備，等待下一次判斷。" :
              `還差${d.upgrade_hint.any ? "以下任一項" : ""}：${d.upgrade_hint.missing.map((k) => CONDITION[k]).join(d.upgrade_hint.any ? "或" : "、")}。`}
          </p>
        )}
      </section>

      <details className="card">
        <summary><h2 style={{ margin: 0 }}>數值與稽核資訊</h2></summary>
        <dl className="kv num" style={{ marginTop: 12 }}>
          <dt>MACD 參數</dt><dd>{(s.params || [12, 26, 9]).join("／")}</dd>
          <dt>快線（DIF）</dt><dd>本週 {fmtNum(s.dif, 4)}；前週 {fmtNum(s.dif_prev, 4)}</dd>
          <dt>慢線（DEA）</dt><dd>本週 {fmtNum(s.dea, 4)}；前週 {fmtNum(s.dea_prev, 4)}</dd>
          <dt>柱體</dt><dd>本週 {fmtNum(s.hist, 4)}；前週 {fmtNum(s.hist_prev, 4)}</dd>
          <dt>容差</dt><dd>{d.eps?.toExponential(2)}</dd>
          <dt>MA5／10／20／60</dt><dd>{["5", "10", "20", "60"].map((k) => fmtNum(s.ma?.[k])).join("／")}</dd>
          <dt>量比</dt><dd>{fmtNum(s.rvol)}</dd>
          <dt>資料版本</dt><dd><code>{d.versions.input_hash}</code>（修訂 {d.versions.data_revision}）</dd>
          <dt>規則版本</dt><dd>{d.versions.rule_version === 0 ? "預設" : `第 ${d.versions.rule_version} 版`}</dd>
          <dt>引擎版本</dt><dd>{d.versions.engine}</dd>
          <dt>狀態歷程</dt><dd>{d.history.map((h) => `${EVENT_STATUS[h.to]}（${fmtDate(h.at)}）`).join(" → ")}</dd>
        </dl>
      </details>

      <div className="row">
        <Link className="btn primary" to={`/instruments/${d.instrument.id}`}>開啟圖表</Link>
        <Link className="btn" to="/positions">查看持股</Link>
        <Link className="btn" to={`/instruments/${d.instrument.id}?tab=rules`}>調整此股規則</Link>
      </div>
    </div>
  );
}
