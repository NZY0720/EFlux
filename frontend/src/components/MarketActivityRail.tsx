import { ListChecks, MessagesSquare, Scale } from "lucide-react";
import { useRef, useState, type KeyboardEvent } from "react";

import type { MarketEvent, MarketSnapshot } from "../api/types";
import Chatroom from "./Chatroom";
import { CardTitle, DashboardCard } from "./DashboardCard";
import OrderBookDepth from "./OrderBookDepth";
import TradeTape from "./TradeTape";

type TabId = "book" | "trades" | "agents";

const P2P_TABS: { id: TabId; label: string }[] = [
  { id: "book", label: "Order book" },
  { id: "trades", label: "Trade activity" },
  { id: "agents", label: "Agent activity" },
];

const REALPRICE_TABS: { id: TabId; label: string }[] = [
  { id: "trades", label: "Grid trades" },
  { id: "agents", label: "Agent activity" },
];

export default function MarketActivityRail({
  snapshot,
  events,
  variant,
}: {
  snapshot: MarketSnapshot | null;
  events: MarketEvent[];
  variant: "p2p" | "realprice";
}) {
  const tabs = variant === "realprice" ? REALPRICE_TABS : P2P_TABS;
  const [active, setActive] = useState<TabId>(variant === "realprice" ? "trades" : "book");
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const title = variant === "realprice" ? "Grid exchange" : "Order flow";

  const onTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next: number | null = null;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = tabs.length - 1;
    if (next === null) return;
    event.preventDefault();
    setActive(tabs[next].id);
    tabRefs.current[next]?.focus();
  };

  return (
    <DashboardCard className="min-w-0">
      <CardTitle icon={active === "agents" ? MessagesSquare : active === "trades" ? ListChecks : Scale}>{title}</CardTitle>
      <div className="inline-flex w-full overflow-hidden rounded-md border border-[var(--border)] bg-[var(--surface-inset)]" role="tablist" aria-label={`${title} views`}>
        {tabs.map((tab, index) => {
          const selected = active === tab.id;
          return (
            <button
              key={tab.id}
              ref={(node) => { tabRefs.current[index] = node; }}
              id={`market-activity-tab-${tab.id}`}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={`market-activity-panel-${tab.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => setActive(tab.id)}
              onKeyDown={(event) => onTabKeyDown(event, index)}
              className={`min-w-0 flex-1 px-2 py-2 text-xs font-medium transition-colors duration-200 ${selected ? "bg-[var(--accent-soft)] text-[var(--accent)]" : "text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text)]"}`}
            >
              {tab.label}
            </button>
          );
        })}
      </div>
      <div id={`market-activity-panel-${active}`} role="tabpanel" aria-labelledby={`market-activity-tab-${active}`} className="mt-3 min-w-0">
        {active === "book" && <OrderBookDepth snapshot={snapshot} />}
        {active === "trades" && <TradeTape events={events} limit={18} compact />}
        {active === "agents" && <Chatroom />}
      </div>
    </DashboardCard>
  );
}
