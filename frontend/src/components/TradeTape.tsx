import { ListChecks } from "lucide-react";

import type { ExternalTradeEvent, MarketEvent, TradeEvent } from "../api/types";
import { formatPrice, formatQuantity } from "../lib/format";
import { useMarket } from "../state/marketStream";
import { EmptyState, TableShell } from "./DashboardCard";

interface Props {
  events: MarketEvent[];
  limit?: number;
  compact?: boolean;
}

type TapeTrade = TradeEvent | ExternalTradeEvent;

function isTrade(e: MarketEvent): e is TapeTrade {
  return e.kind === "trade" || e.kind === "external.trade";
}

export default function TradeTape({ events, limit = 25, compact = false }: Props) {
  const { nameOf } = useMarket();
  const trades = events.filter(isTrade).slice(0, limit);

  return (
    <TableShell className="h-72">
      <table className="eflux-table text-xs">
        <thead className="sticky top-0 z-10">
          <tr>
            <th className="px-3 py-2 text-left font-semibold">Time</th>
            <th className="px-3 py-2 text-right font-semibold">Price ($/MWh)</th>
            <th className="px-3 py-2 text-right font-semibold">Qty (kWh)</th>
            <th className={compact ? "hidden px-3 py-2 text-left font-semibold sm:table-cell" : "px-3 py-2 text-left font-semibold"}>Buyer</th>
            <th className={compact ? "hidden px-3 py-2 text-left font-semibold sm:table-cell" : "px-3 py-2 text-left font-semibold"}>Seller</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => {
            const dt = new Date(t.wall_ts);
            const key = t.kind === "trade" ? `p2p-${t.trade_id}` : `external-${t.external_trade_id}`;
            const buyer = t.kind === "trade"
              ? nameOf(t.buy_vpp_id)
              : t.side === "buy"
                ? nameOf(t.vpp_id)
                : t.counterparty;
            const seller = t.kind === "trade"
              ? nameOf(t.sell_vpp_id)
              : t.side === "buy"
                ? t.counterparty
                : nameOf(t.vpp_id);
            return (
              <tr key={key}>
                <td className="px-3 py-1.5 text-[var(--text-muted)] tabular-nums">
                  {dt.toLocaleTimeString("en-GB", { hour12: false })}
                </td>
                <td className="px-3 py-1.5 text-right font-semibold text-[var(--warning)] tabular-nums">{formatPrice(t.price)}</td>
                <td className="px-3 py-1.5 text-right text-[var(--text)] tabular-nums">{formatQuantity(t.qty)}</td>
                <td className={compact ? "hidden px-3 py-1.5 text-[var(--success)] sm:table-cell" : "px-3 py-1.5 text-[var(--success)]"}>{buyer}</td>
                <td className={compact ? "hidden px-3 py-1.5 text-[var(--danger)] sm:table-cell" : "px-3 py-1.5 text-[var(--danger)]"}>{seller}</td>
              </tr>
            );
          })}
          {trades.length === 0 && (
            <tr>
              <td colSpan={5} className="p-3">
                <EmptyState icon={ListChecks} title="Waiting for trades..." />
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </TableShell>
  );
}
