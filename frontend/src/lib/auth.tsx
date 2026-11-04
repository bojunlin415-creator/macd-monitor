import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api } from "./api";

export interface Me {
  id: string; email: string; display_name: string; role: string; theme: string; updown_color: string; table_density: string;
  consent_version: string | null; consent_required: string; onboarding_done: boolean;
  plan: { id: string; name: string };
  line: { status: string; linked_at: string | null };
  notify: { muted_codes: string[]; merge_threshold: number; weekly_summary: boolean; paused_until: string | null; mandatory_codes: string[] };
}

interface Ctx { me: Me | null; loading: boolean; refresh: () => Promise<void>; setMe: (m: Me | null) => void }
const AuthCtx = createContext<Ctx>({ me: null, loading: true, refresh: async () => {}, setMe: () => {} });

export function applyAppearance(me: Me | null) {
  const root = document.documentElement;
  const theme = me?.theme ?? "system";
  if (theme === "system") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", theme);
  root.setAttribute("data-updown", me?.updown_color ?? "red_up");
  document.body.classList.toggle("compact", me?.table_density === "compact");
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMeState] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const setMe = (m: Me | null) => { setMeState(m); applyAppearance(m); };
  const refresh = useCallback(async () => {
    try { setMe((await api<Me>("/me")).data); } catch { setMe(null); } finally { setLoading(false); }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  return <AuthCtx.Provider value={{ me, loading, refresh, setMe }}>{children}</AuthCtx.Provider>;
}

export const useAuth = () => useContext(AuthCtx);
