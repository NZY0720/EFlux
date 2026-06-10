import type { MarketEvent, TradeEvent } from "../api/types";
import { useMarket } from "../state/marketStream";

interface Props {
  events: MarketEvent[];
  limit?: number;
}

function isTrade(e: MarketEvent): e is TradeEvent {
  return e.kind === "trade";
}

export default function TradeTape({ events, limit = 25 }: Props) {
  const { nameOf } = useMarket();
  const trades = events.filter(isTrade).slice(0, limit);

  return (
    <div className="h-72 overflow-auto rounded border border-slate-800 bg-slate-900/60">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-slate-900 text-slate-400">
          <tr>
            <th className="px-3 py-2 text-left">Time</th>
            <th className="px-3 py-2 text-right">Price</th>
            <th className="px-3 py-2 text-right">Qty</th>
            <th className="px-3 py-2 text-left">Buyer</th>
            <th className="px-3 py-2 text-left">Seller</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => {
            const dt = new Date(t.wall_ts);
            return (
              <tr key={t.trade_id} className="border-t border-slate-800 hover:bg-slate-800/50">
                <td className="px-3 py-1.5 text-slate-300 tabular-nums">
                  {dt.toLocaleTimeString("en-GB", { hour12: false })}
                </td>
                <td className="px-3 py-1.5 text-right text-amber-300 tabular-nums">{Number(t.price).toFixed(2)}</td>
                <td className="px-3 py-1.5 text-right text-slate-200 tabular-nums">{Number(t.qty).toFixed(3)}</td>
                <td className="px-3 py-1.5 text-emerald-300">{nameOf(t.buy_vpp_id)}</td>
                <td className="px-3 py-1.5 text-rose-300">{nameOf(t.sell_vpp_id)}</td>
              </tr>
            );
          })}
          {trades.length === 0 && (
            <tr>
              <td colSpan={5} className="px-3 py-4 text-center text-slate-500">
                Waiting for trades…
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
