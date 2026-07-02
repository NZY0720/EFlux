import { useEffect, useState } from "react";
import { MessagesSquare } from "lucide-react";

import { fetchChatter } from "../api/client";
import type { ChatMessage } from "../api/types";
import { EmptyState } from "./DashboardCard";

/**
 * Agent chatroom — the LLM-steered agents' casual small talk about the market and each
 * other, each in its own deployed model's voice. Only name + timestamp + message are
 * shown (no strategy/PnL leakage). Newest first, polled every 5s.
 */

// Stable per-agent badge color: hash of the name, so a name keeps its color across polls.
const BADGES = [
  "#059669",
  "#0284c7",
  "#7c3aed",
  "#d97706",
  "#e11d48",
  "#0d9488",
  "#9333ea",
  "#0891b2",
];

function colorForName(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) | 0;
  return BADGES[Math.abs(hash) % BADGES.length];
}

export default function Chatroom() {
  const [messages, setMessages] = useState<ChatMessage[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await fetchChatter(40);
        if (!cancelled) setMessages(r);
      } catch {
        /* transient — keep showing the last messages */
      }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <div className="flex h-72 flex-col">
      <div className="mb-2 text-xs text-[var(--text-muted)]">
        The LLM-steered agents trash-talk and small-talk about the market — each in its own
        model&apos;s voice.
      </div>
      <div className="eflux-inset min-h-0 flex-1 space-y-2 overflow-auto rounded-lg p-2">
        {messages === null && (
          <EmptyState icon={MessagesSquare} title="Loading chatroom..." className="min-h-full" />
        )}
        {messages !== null && messages.length === 0 && (
          <div className="px-1 py-4 text-center text-xs text-[var(--text-subtle)]">
            Quiet for now. The agents chat every few seconds when the LLM link is live.
          </div>
        )}
        {messages?.map((m, i) => {
          const color = m.color || colorForName(m.name);
          return (
            <div key={`${m.wall_ts}-${i}`} className="flex gap-2">
              {m.avatar ? (
                <span className="mt-0.5 w-4 shrink-0 text-center text-[11px] leading-4">{m.avatar}</span>
              ) : (
                <span
                  className="mt-1 ml-1 inline-block h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: color }}
                />
              )}
              <div className="min-w-0">
                <div className="flex items-baseline gap-2">
                  <span className="text-xs font-semibold" style={{ color }}>
                    {m.name}
                  </span>
                  {m.source === "owner" && (
                    <span
                      className="rounded-full border px-1.5 text-[9px] font-medium uppercase tracking-wide"
                      style={{ color, borderColor: color }}
                      title="Typed by the agent's owner"
                    >
                      op
                    </span>
                  )}
                  <span className="text-[10px] text-[var(--text-subtle)] tabular-nums">
                    {new Date(m.wall_ts).toLocaleTimeString("en-GB", { hour12: false })}
                  </span>
                </div>
                <p className="text-xs text-[var(--text)]">{m.text}</p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
