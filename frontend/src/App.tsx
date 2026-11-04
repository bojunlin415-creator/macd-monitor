import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { ReactNode } from "react";
import { AuthProvider, useAuth } from "./lib/auth";
import { Skeleton, useApi } from "./components/ui";
import Login, { AcceptInvite, Setup, Verify } from "./pages/Login";
import Onboarding from "./pages/Onboarding";
import Overview from "./pages/Overview";
import Signals from "./pages/Signals";
import SignalDetail from "./pages/SignalDetail";
import InstrumentPage from "./pages/Instrument";
import Watchlist from "./pages/Watchlist";
import Positions from "./pages/Positions";
import Settings from "./pages/Settings";
import Admin from "./pages/Admin";
import Membership from "./pages/Membership";
import More, { Backtest, DataHelp } from "./pages/More";

const NAV = [
  { to: "/", label: "總覽", ico: "◎", end: true },
  { to: "/signals", label: "訊號", ico: "◆", badge: true },
  { to: "/watchlist", label: "自選", ico: "☆" },
  { to: "/positions", label: "持股", ico: "▣" },
  { to: "/backtest", label: "回測", ico: "↺", desktop: true },
  { to: "/settings", label: "設定", ico: "⚙", desktop: true },
  { to: "/admin", label: "管理中心", ico: "⌂", desktop: true, admin: true },
];

function Shell({ children }: { children: ReactNode }) {
  const { me } = useAuth();
  const unread = useApi<any[]>("/signals?limit=1", [useLocation().pathname]).meta.unread ?? 0;
  const items = NAV.filter((n) => !n.admin || me?.role === "admin");
  return (
    <div className="app">
      <nav className="sidenav" aria-label="主選單">
        <div className="brand">MACD 監測</div>
        {items.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => `navlink ${isActive ? "active" : ""}`} title={n.label}>
            <span className="ico" aria-hidden>{n.ico}</span><span className="lbl">{n.label}</span>
            {n.badge && unread > 0 && <span className="navbadge" aria-label={`${unread} 則未讀`}>{unread}</span>}
          </NavLink>
        ))}
      </nav>
      <main className="main" id="main">{children}</main>
      <nav className="bottomnav" aria-label="主選單">
        {items.filter((n) => !n.desktop).map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "active" : "")}>
            <span aria-hidden>{n.ico}</span>{n.label}
            {n.badge && unread > 0 && <span className="navbadge">{unread}</span>}
          </NavLink>
        ))}
        <NavLink to="/more" className={({ isActive }) => (isActive ? "active" : "")}><span aria-hidden>…</span>更多</NavLink>
      </nav>
    </div>
  );
}

function Protected({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="main"><Skeleton /></div>;
  if (!me) return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  if ((!me.consent_version || !me.onboarding_done) && loc.pathname !== "/onboarding") return <Navigate to="/onboarding" replace />;
  return <Shell>{children}</Shell>;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/login/verify" element={<Verify />} />
        <Route path="/invite" element={<AcceptInvite />} />
        <Route path="/setup" element={<Setup />} />
        <Route path="/onboarding" element={<OnboardingGate />} />
        <Route path="/" element={<Protected><Overview /></Protected>} />
        <Route path="/signals" element={<Protected><Signals /></Protected>} />
        <Route path="/signals/:id" element={<Protected><SignalDetail /></Protected>} />
        <Route path="/instruments/:id" element={<Protected><InstrumentPage /></Protected>} />
        <Route path="/watchlist" element={<Protected><Watchlist /></Protected>} />
        <Route path="/positions" element={<Protected><Positions /></Protected>} />
        <Route path="/membership" element={<Protected><Membership /></Protected>} />
        <Route path="/backtest" element={<Protected><Backtest /></Protected>} />
        <Route path="/settings" element={<Protected><Settings /></Protected>} />
        <Route path="/admin" element={<Protected><Admin /></Protected>} />
        <Route path="/more" element={<Protected><More /></Protected>} />
        <Route path="/help/data" element={<Protected><DataHelp /></Protected>} />
        <Route path="*" element={<Protected><div className="card empty"><p>找不到這個頁面，或你沒有查看權限。</p></div></Protected>} />
      </Routes>
    </AuthProvider>
  );
}

function OnboardingGate() {
  const { me, loading } = useAuth();
  if (loading) return <Skeleton />;
  if (!me) return <Navigate to="/login" replace />;
  return <Onboarding />;
}
