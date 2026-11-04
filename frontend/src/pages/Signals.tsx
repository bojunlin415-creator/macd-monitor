import { useSearchParams } from "react-router-dom";
import { Empty, ErrorBox, SignalCard, SignalEvent, Skeleton, useApi } from "../components/ui";
import { DIRECTION, EVENT_STATUS, LEVEL, MARKET, weekTitle } from "../i18n";

const FILTERS: { key: string; label: string; options: Record<string, string> }[] = [
  { key: "level", label: "等級", options: LEVEL },
  { key: "direction", label: "方向", options: DIRECTION },
  { key: "market", label: "市場", options: MARKET },
  { key: "status", label: "狀態", options: EVENT_STATUS },
  { key: "held", label: "持股", options: { true: "只看持有", false: "未持有" } },
];

// 訊號中心：篩選條件寫入網址，便於分享與返回
export default function Signals() {
  const [params, setParams] = useSearchParams();
  const qs = new URLSearchParams(params);
  qs.set("limit", "200");
  const { data, error, loading, reload } = useApi<SignalEvent[]>(`/signals?${qs.toString()}`);
  const toggle = (key: string, val: string) => {
    const next = new URLSearchParams(params);
    if (key === "direction" || key === "held") {
      next.get(key) === val ? next.delete(key) : next.set(key, val);
    } else {
      const cur = new Set((next.get(key) || "").split(",").filter(Boolean));
      cur.has(val) ? cur.delete(val) : cur.add(val);
      cur.size ? next.set(key, [...cur].join(",")) : next.delete(key);
    }
    setParams(next, { replace: true });
  };
  const isOn = (key: string, val: string) => (params.get(key) || "").split(",").includes(val);
  const groups = new Map<string, SignalEvent[]>();
  (data || []).forEach((e) => { groups.set(e.week_key, [...(groups.get(e.week_key) || []), e]); });
  return (
    <>
      <div className="topbar"><h1>訊號</h1>{params.toString() && <button className="btn link" onClick={() => setParams({})}>清除篩選</button>}</div>
      <div className="stack" style={{ gap: 8, marginBottom: 16 }}>
        {FILTERS.map((f) => (
          <div key={f.key} className="chipgroup" role="group" aria-label={f.label}>
            <span>{f.label}</span>
            {Object.entries(f.options).map(([v, l]) => (
              <button key={v} className="chip" aria-pressed={isOn(f.key, v)} onClick={() => toggle(f.key, v)}>{l}</button>
            ))}
          </div>
        ))}
      </div>
      {error && <ErrorBox error={error} onRetry={reload} />}
      {loading && !data ? <Skeleton /> : data && data.length === 0 ? <Empty text="沒有符合篩選條件的訊號。" /> : (
        [...groups.entries()].map(([wk, evs]) => (
          <section key={wk} className="section">
            <h2>{weekTitle(wk)}</h2>
            <div className="grid">{evs.map((e) => <SignalCard key={e.id} e={e} />)}</div>
          </section>
        ))
      )}
    </>
  );
}
