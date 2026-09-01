import { useEffect, useState, useCallback } from "react";
import { Activity, ShieldOff } from "lucide-react";
import { api } from "@/lib/api";
import { PageHeader, Panel } from "@/components/common";

export default function Monitoring() {
  const [data, setData] = useState(null);
  const load = useCallback(async () => {
    const { data } = await api.get("/monitoring/services");
    setData(data);
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);

  const boundaries = data?.boundaries || {};

  return (
    <div data-testid="monitoring-page">
      <PageHeader title="Monitoring" subtitle="Service health and regulated-capability boundaries." />
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 md:gap-6 mb-6">
        {(data?.services || []).map((s) => (
          <Panel key={s.name} className="cp-anim" data-testid={`service-${s.name}`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className={`h-2 w-2 rounded-full ${s.status === "up" ? "bg-emerald-400 animate-pulse" : "bg-red-400"}`} />
                <p className="text-sm font-medium">{s.name}</p>
              </div>
              <Activity className="h-4 w-4 text-muted-foreground" />
            </div>
            <p className="font-mono text-xs text-muted-foreground mt-3">
              {s.status === "up" ? "OPERATIONAL" : "DOWN"}{s.latency_ms != null ? ` · ${s.latency_ms}ms` : ""}
            </p>
          </Panel>
        ))}
      </div>

      <h3 className="font-heading text-lg font-medium mb-3">Regulated Capability Boundaries</h3>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 md:gap-6">
        {[
          { key: "kyc_aml", label: "KYC / AML", state: boundaries.kyc_aml?.configured, note: boundaries.kyc_aml?.note },
          { key: "vda", label: "Digital Assets (VDA)", state: boundaries.vda?.enabled, note: boundaries.vda?.note },
          { key: "ai_voice", label: "AI / Voice", state: boundaries.ai_voice?.enabled, note: boundaries.ai_voice?.note },
        ].map((b) => (
          <Panel key={b.key} data-testid={`boundary-${b.key}`}>
            <div className="flex items-center justify-between">
              <p className="font-medium">{b.label}</p>
              <span className="inline-flex items-center gap-1.5 text-xs font-mono px-2.5 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20">
                <ShieldOff className="h-3 w-3" /> {b.state ? "ENABLED" : "DISABLED"}
              </span>
            </div>
            <p className="text-sm text-muted-foreground mt-3">{b.note}</p>
          </Panel>
        ))}
      </div>
    </div>
  );
}
