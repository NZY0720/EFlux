import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
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
  restoring: boolean;
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
  const [restoring, setRestoring] = useState(true);
  const sessionEpochRef = useRef(0);

  useEffect(() => {
    if (email) localStorage.setItem(EMAIL_KEY, email);
    else localStorage.removeItem(EMAIL_KEY);
  }, [email]);

  useEffect(() => {
    if (userId !== null) localStorage.setItem(USER_ID_KEY, String(userId));
    else localStorage.removeItem(USER_ID_KEY);
  }, [userId]);

  const setSession: AuthCtx["setSession"] = (s) => {
    sessionEpochRef.current += 1;
    // The server has set the HttpOnly cookie; do not persist this transitional
    // response token in browser storage.
    setStoredToken(null);
    setTokState(COOKIE_SESSION);
    setEmail(s.email);
    setUserId(s.user_id);
  };

  const logout = useCallback(() => {
    sessionEpochRef.current += 1;
    setTokState(null);
    setEmail(null);
    setUserId(null);
    // Keep a migrated localStorage token available for this request, then remove
    // it whether server logout succeeds or the browser is offline.
    void endSession().finally(() => setStoredToken(null)).catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    const epoch = sessionEpochRef.current;
    void getCurrentUser()
      .then((user) => {
        if (cancelled || sessionEpochRef.current !== epoch) return;
        setEmail(user.email);
        setUserId(user.id);
        if (!getToken()) setTokState(COOKIE_SESSION);
      })
      .catch(() => {
        // An unauthenticated boot is normal; the response interceptor handles
        // expired localStorage sessions.
      })
      .finally(() => {
        if (!cancelled) setRestoring(false);
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
    <Ctx.Provider value={{ token, email, userId, restoring, setSession, logout }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAuth(): AuthCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used within AuthProvider");
  return v;
}
