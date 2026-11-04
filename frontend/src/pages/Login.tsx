import { FormEvent, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError, setCsrf } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Field } from "../components/ui";

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 16 }}>
      <div className="card" style={{ maxWidth: 420, width: "100%" }}>
        <h1>MACD 監測系統</h1>
        {children}
      </div>
    </div>
  );
}

const LOGIN_ERRORS: Record<string, string> = {
  not_invited: "這個 Google 帳號還沒有受邀。請先請管理員邀請你的 Gmail，再用 Google 登入。",
  google_failed: "Google 登入沒有完成，請再試一次。",
  google_cancelled: "已取消 Google 登入。",
  google_disabled: "Google 登入尚未開放，請改用 Email 登入連結。",
};

export default function Login() {
  const [params] = useSearchParams();
  const [google, setGoogle] = useState(false);
  useEffect(() => {
    api<{ google: boolean }>("/auth/providers").then((r) => setGoogle(r.data.google)).catch(() => setGoogle(false));
  }, []);
  const loginError = LOGIN_ERRORS[params.get("error") ?? ""];
  const [email, setEmail] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [devLink, setDevLink] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      const r = await api<{ message: string; dev_link: string | null }>("/auth/email-link", { method: "POST", body: { email } });
      setMsg(r.data.message); setDevLink(r.data.dev_link);
    } catch (e) { setErr((e as ApiError).message); } finally { setBusy(false); }
  }
  return (
    <Frame>
      <p className="muted">僅限受邀成員使用。</p>
      {loginError && <p className="banner" role="alert">{loginError}</p>}
      {google && (
        <>
          <a className="btn primary" style={{ display: "block", textAlign: "center" }} href="/api/v1/auth/google/start">使用 Google 帳號登入</a>
          <p className="tiny muted" style={{ textAlign: "center" }}>或使用 Email 登入連結</p>
        </>
      )}
      <form onSubmit={submit} className="stack">
        <Field label="Email" error={err ?? undefined}>
          <input className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
        </Field>
        <button className={google ? "btn" : "btn primary"} disabled={busy}>{busy ? "寄送中…" : "寄送登入連結"}</button>
      </form>
      {msg && <p role="status">{msg}</p>}
      {devLink && <p className="small">開發模式：<a href={devLink.replace(/^https?:\/\/[^/]+/, "")}>直接開啟登入連結</a></p>}
    </Frame>
  );
}

function useTokenLogin(path: string) {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const { refresh } = useAuth();
  const [err, setErr] = useState<string | null>(null);
  const ran = useRef(false);
  useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    (async () => {
      try {
        const r = await api<{ csrf_token: string }>(path, { method: "POST", body: { token: params.get("token") } });
        setCsrf(r.data.csrf_token);
        await refresh();
        nav("/", { replace: true });
      } catch (e) { setErr((e as ApiError).message); }
    })();
  }, [params, nav, refresh, path]);
  return err;
}

export function Verify() {
  const err = useTokenLogin("/auth/verify");
  return <Frame>{err ? <><p role="alert">{err}</p><a className="btn" href="/login">重新取得登入連結</a></> : <p>登入中…</p>}</Frame>;
}

export function AcceptInvite() {
  const err = useTokenLogin("/auth/accept-invite");
  return <Frame>{err ? <p role="alert">{err}</p> : <p>正在接受邀請…</p>}</Frame>;
}

// 部署後首次登入：輸入在 Vercel 設定的 CRON_SECRET，取得管理員一次性登入連結
export function Setup() {
  const [email, setEmail] = useState("");
  const [secret, setSecret] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const r = await api<{ link: string }>("/auth/bootstrap-link", { method: "POST", body: { email }, headers: { Authorization: `Bearer ${secret.trim()}` } });
      window.location.href = r.data.link.replace(/^https?:\/\/[^/]+/, "");
    } catch {
      setErr("設定金鑰或 Email 不正確。");
    } finally { setBusy(false); }
  }
  return (
    <Frame>
      <p className="muted">管理員首次登入。請輸入你的 Email，以及在 Vercel 環境變數中設定的 CRON_SECRET。系統還沒有管理員時，會以這個 Email 建立第一位管理員。</p>
      <form onSubmit={submit} className="stack">
        <Field label="Email"><input className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
        <Field label="設定金鑰（CRON_SECRET）" error={err ?? undefined}><input className="input" type="password" required value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off" /></Field>
        <button className="btn primary" disabled={busy}>{busy ? "驗證中…" : "登入"}</button>
      </form>
    </Frame>
  );
}
