import { useEffect, useState } from "react";
import { AlertCircle, Terminal } from "lucide-react";

import { listVPPs } from "../api/client";
import type { VPP } from "../api/types";
import { CardTitle, DashboardCard } from "../components/DashboardCard";
import { ApiAutomationCard } from "./vpps/LegacyVppParts";

export default function DeveloperConsole() {
  const [vpps, setVpps] = useState<VPP[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { listVPPs().then(setVpps).catch((err: Error) => setError(err.message)); }, []);
  return <div className="mx-auto w-full max-w-[1800px] space-y-6 px-4 py-5 md:p-6">
    <DashboardCard><CardTitle icon={Terminal}>Developer Console</CardTitle><p className="text-sm text-[var(--text-muted)]">Connect an external application to your manually controlled VPPs.</p></DashboardCard>
    <ApiAutomationCard vpps={vpps} onError={setError} />
    {error && <div className="flex items-start gap-2 rounded-lg bg-[var(--danger-soft)] p-3 text-sm text-[var(--danger)]"><AlertCircle size={17} className="mt-0.5 shrink-0" />{error}</div>}
  </div>;
}
