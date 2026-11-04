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

export default function Login() {
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
      <p className="muted">僅限受邀成員使用。輸入 Email，我們會寄出一次性登入連結。</p>
      <form onSubmit={submit} className="stack">
        <Field label="Email" error={err ?? undefined}>
          <input className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
        </Field>
        <button className="btn primary" disabled={busy}>{busy ? "寄送中…" : "寄送登入連結"}</button>
      </form>
      {msg && <p role="status">{msg}</p>}
      {devLink && <p className="small">開發模式：<a href={devLink.replace(/^https?:\/\/[^/]+/, "")}>直接開啟登入連結</a></p>}
      <p className="tiny muted">Google 登入將於下一階段開放。</p>
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
