import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

import { getToken, setToken as setStoredToken } from "../api/client";

interface AuthCtx {
  token: string | null;
  email: string | null;
  userId: number | null;
  setSession: (s: { session_token: string; user_id: number; email: string }) => void;
  logout: () => void;
}

const Ctx = createContext<AuthCtx | null>(null);

const EMAIL_KEY = "eflux.email";
const USER_ID_KEY = "eflux.user_id";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokState] = useState<string | null>(() => getToken());
  const [email, setEmail] = useState<string | null>(() => localStorage.getItem(EMAIL_KEY));
  const [userId, setUserId] = useState<number | null>(() => {
    const v = localStorage.getItem(USER_ID_KEY);
    return v ? Number(v) : null;
  });

  useEffect(() => {
    if (email) localStorage.setItem(EMAIL_KEY, email);
    else localStorage.removeItem(EMAIL_KEY);
  }, [email]);

  useEffect(() => {
    if (userId !== null) localStorage.setItem(USER_ID_KEY, String(userId));
    else localStorage.removeItem(USER_ID_KEY);
  }, [userId]);

  const setSession: AuthCtx["setSession"] = (s) => {
    setStoredToken(s.session_token);
    setTokState(s.session_token);
    setEmail(s.email);
    setUserId(s.user_id);
  };

  const logout = () => {
    setStoredToken(null);
    setTokState(null);
    setEmail(null);
    setUserId(null);
  };

  return (
    <Ctx.Provider value={{ token, email, userId, setSession, logout }}>{children}</Ctx.Provider>
  );
}

export function useAuth(): AuthCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used within AuthProvider");
  return v;
}
