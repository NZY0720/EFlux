import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { consumeToken, requestMagicLink } from "../api/client";
import { useAuth } from "../state/auth";

type Step = "email" | "token";

export default function Login() {
  const auth = useAuth();
  const nav = useNavigate();
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [token, setToken] = useState("");
  const [devToken, setDevToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submitEmail = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const r = await requestMagicLink(email.trim());
      if (r.dev_token) {
        setDevToken(r.dev_token);
        setToken(r.dev_token);
      }
      setStep("token");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const submitToken = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const session = await consumeToken(token.trim());
      auth.setSession(session);
      nav("/");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-md rounded-xl border border-slate-800 bg-slate-900/60 p-8 shadow-2xl">
        <h1 className="text-2xl font-semibold text-white mb-1">Sign in</h1>
        <p className="text-sm text-slate-400 mb-6">Passwordless — magic link to your email.</p>

        {step === "email" && (
          <form onSubmit={submitEmail} className="space-y-4">
            <label className="block">
              <span className="text-sm text-slate-300">Email</span>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-3 py-2 text-white outline-none focus:border-sky-500"
                placeholder="you@hku.hk"
                autoFocus
              />
            </label>
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded bg-sky-600 hover:bg-sky-500 disabled:opacity-50 px-4 py-2 text-white font-medium"
            >
              {busy ? "Sending…" : "Send magic link"}
            </button>
          </form>
        )}

        {step === "token" && (
          <form onSubmit={submitToken} className="space-y-4">
            {devToken && (
              <div className="rounded border border-amber-700/60 bg-amber-950/40 p-3 text-xs text-amber-200">
                <div className="font-semibold mb-1">Dev mode</div>
                Token auto-filled below — paste a real one from your email in production.
              </div>
            )}
            <label className="block">
              <span className="text-sm text-slate-300">Token</span>
              <input
                type="text"
                required
                value={token}
                onChange={(e) => setToken(e.target.value)}
                className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-3 py-2 text-white outline-none focus:border-sky-500 font-mono text-xs"
                autoFocus
              />
            </label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setStep("email")}
                className="rounded border border-slate-700 hover:border-slate-500 px-4 py-2 text-slate-300"
              >
                Back
              </button>
              <button
                type="submit"
                disabled={busy}
                className="flex-1 rounded bg-sky-600 hover:bg-sky-500 disabled:opacity-50 px-4 py-2 text-white font-medium"
              >
                {busy ? "Signing in…" : "Sign in"}
              </button>
            </div>
          </form>
        )}

        {error && <p className="mt-4 text-sm text-rose-400">{error}</p>}
      </div>
    </div>
  );
}
