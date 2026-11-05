// API 用戶端：統一回應 {data, meta, error}；錯誤訊息由後端以繁中回傳。
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public fieldErrors: Record<string, string> = {}, public requestId?: string) {
    super(message);
  }
}

let csrf = sessionStorageGet("csrf") || "";
function sessionStorageGet(k: string): string | null {
  try { return sessionStorage.getItem(k); } catch { return null; }
}
export function setCsrf(t: string) {
  csrf = t;
  try { sessionStorage.setItem("csrf", t); } catch { /* 無痕模式 */ }
}

export interface Envelope<T> { data: T; meta: Record<string, any>; error: null | { code: string; message: string; field_errors?: Record<string, string> } }

export async function api<T = any>(path: string, opts: { method?: string; body?: any; form?: FormData; headers?: Record<string, string> } = {}): Promise<Envelope<T>> {
  const method = opts.method || "GET";
  const headers: Record<string, string> = { ...(opts.headers || {}) };
  if (method !== "GET" && csrf) headers["X-CSRF-Token"] = csrf;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.body); }
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, { method, headers, body, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "network", "連線失敗，請檢查網路後重新載入。");
  }
  let json: any = null;
  try { json = await res.json(); } catch { /* 非 JSON */ }
  if (!res.ok || json?.error) {
    if (res.status === 403 && json?.error?.code === "csrf") {
      // 重新取得 CSRF 後重試一次
      const r = await fetch("/api/v1/auth/csrf", { credentials: "same-origin" });
      if (r.ok) { setCsrf((await r.json()).data.csrf_token); return api(path, opts); }
    }
    throw new ApiError(res.status, json?.error?.code || "error", json?.error?.message || "系統發生錯誤，請稍後再試。", json?.error?.field_errors || {}, res.headers.get("X-Request-Id") || undefined);
  }
  return json as Envelope<T>;
}

export function idemKey(): string {
  return (crypto as any).randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random();
}
