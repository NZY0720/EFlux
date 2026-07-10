import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

import {
  getCurrentUser,
  getToken,
  logout as endSession,
  setAuthExpiredHandler,
  setToken as setStoredToken,
} from "../api/client";

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
const COOKIE_SESSION = "cookie-session";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokState] = useState<string | null>(() => getToken());
  const [email, setEmail] = useState<string | null>(null);
  const [userId, setUserId] = useState<number | null>(null);

  useEffect(() => {
    if (email) localStorage.setItem(EMAIL_KEY, email);
    else localStorage.removeItem(EMAIL_KEY);
  }, [email]);

  useEffect(() => {
    if (userId !== null) localStorage.setItem(USER_ID_KEY, String(userId));
    else localStorage.removeItem(USER_ID_KEY);
  }, [userId]);

  const setSession: AuthCtx["setSession"] = (s) => {
    // The server has set the HttpOnly cookie; do not persist this transitional
    // response token in browser storage.
    setStoredToken(null);
    setTokState(COOKIE_SESSION);
    setEmail(s.email);
    setUserId(s.user_id);
  };

  const logout = useCallback(() => {
    setTokState(null);
    setEmail(null);
    setUserId(null);
    // Keep a migrated localStorage token available for this request, then remove
    // it whether server logout succeeds or the browser is offline.
    void endSession().finally(() => setStoredToken(null));
  }, []);

  useEffect(() => {
    let cancelled = false;
    void getCurrentUser()
      .then((user) => {
        if (cancelled) return;
        setEmail(user.email);
        setUserId(user.id);
        if (!getToken()) setTokState(COOKIE_SESSION);
      })
      .catch(() => {
        // An unauthenticated boot is normal; the response interceptor handles
        // expired localStorage sessions.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setAuthExpiredHandler(logout);
    return () => setAuthExpiredHandler(null);
  }, [logout]);

  return (
    <Ctx.Provider value={{ token, email, userId, setSession, logout }}>{children}</Ctx.Provider>
  );
}

export function useAuth(): AuthCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used within AuthProvider");
  return v;
}
