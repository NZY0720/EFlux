import { useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft, KeyRound, Mail, Send } from "lucide-react";

import { consumeToken, requestMagicLink } from "../api/client";
import BrandLogo from "../components/BrandLogo";
import { useAuth } from "../state/auth";

type Step = "email" | "token";

export default function Login() {
  const auth = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [searchParams] = useSearchParams();
  const mode = searchParams.get("mode") === "register" ? "register" : "login";
  const from = typeof loc.state === "object" && loc.state && "from" in loc.state ? String(loc.state.from) : "/market";
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
      nav(from);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-[calc(100vh-64px)] items-center justify-center px-4 py-10">
      <div className="eflux-card w-full max-w-md p-8">
        <div className="mb-6 flex items-center gap-3">
          <BrandLogo size={34} />
          <div>
            <h1 className="text-2xl font-semibold text-[var(--text)]">
              {mode === "register" ? "Create account" : "Sign in"}
            </h1>
            <p className="text-sm text-[var(--text-muted)]">
              Passwordless magic link. New emails are registered automatically.
            </p>
          </div>
        </div>

        {step === "email" && (
          <form onSubmit={submitEmail} className="space-y-4">
            <label className="block">
              <span className="flex items-center gap-1.5 text-sm font-medium text-[var(--text-muted)]">
                <Mail size={14} />
                Email
              </span>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="eflux-input mt-1 w-full rounded-md px-3 py-2 text-sm outline-none"
                placeholder="you@hku.hk"
                autoFocus
              />
            </label>
            <button
              type="submit"
              disabled={busy}
              className="eflux-btn eflux-btn-primary h-10 w-full px-4 font-semibold disabled:opacity-50"
            >
              <Send size={16} />
              {busy ? "Sending..." : mode === "register" ? "Create account link" : "Send magic link"}
            </button>
          </form>
        )}

        {step === "token" && (
          <form onSubmit={submitToken} className="space-y-4">
            {devToken && (
              <div className="rounded-lg border border-[color-mix(in_srgb,var(--warning)_42%,transparent)] bg-[var(--warning-soft)] p-3 text-xs text-[var(--warning)]">
                <div className="mb-1 font-semibold">Dev mode</div>
                Token auto-filled below - paste a real one from your email in production.
              </div>
            )}
            <label className="block">
              <span className="flex items-center gap-1.5 text-sm font-medium text-[var(--text-muted)]">
                <KeyRound size={14} />
                Token
              </span>
              <input
                type="text"
                required
                value={token}
                onChange={(e) => setToken(e.target.value)}
                className="eflux-input mt-1 w-full rounded-md px-3 py-2 font-mono text-xs outline-none"
                autoFocus
              />
            </label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setStep("email")}
                className="eflux-btn h-10 px-4"
              >
                <ArrowLeft size={16} />
                Back
              </button>
              <button
                type="submit"
                disabled={busy}
                className="eflux-btn eflux-btn-primary h-10 flex-1 px-4 font-semibold disabled:opacity-50"
              >
                <KeyRound size={16} />
                {busy ? "Signing in..." : mode === "register" ? "Create account" : "Sign in"}
              </button>
            </div>
          </form>
        )}

        {error && <p className="mt-4 text-sm text-[var(--danger)]">{error}</p>}
        <div className="mt-5 flex items-center justify-between gap-3 border-t border-[var(--border)] pt-4 text-sm text-[var(--text-muted)]">
          <Link to="/" className="hover:text-[var(--text)]">
            Back to EFlux
          </Link>
          <Link
            to={mode === "register" ? "/login" : "/login?mode=register"}
            className="font-medium text-[var(--accent)] hover:underline"
          >
            {mode === "register" ? "Already have access?" : "Need an account?"}
          </Link>
        </div>
      </div>
    </div>
  );
}
