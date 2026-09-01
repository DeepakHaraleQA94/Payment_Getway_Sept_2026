import { useEffect, useState, useCallback } from "react";
import { Activity, GitBranch, CheckCircle2, XCircle, ShieldCheck, ShieldOff } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, EmptyState } from "@/components/common";

function HealthDot({ status }) {
  const up = status === "up";
  return <span className={`h-2 w-2 rounded-full ${up ? "bg-emerald-400 animate-pulse" : "bg-red-400"}`} />;
}

function AccountCard({ a }) {
  const m = a.metrics || {};
  const rate = m.success_rate != null ? `${Math.round(m.success_rate * 100)}%` : "—";
  return (
    <Panel data-testid={`health-account-${a.id}`} className="cp-anim">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <HealthDot status={a.health_status} />
          <div>
            <p className="text-sm font-medium">{a.display_name}</p>
            <p className="font-mono text-[11px] text-muted-foreground">{a.provider_key}</p>
          </div>
        </div>
        <span
          data-testid={`health-eligible-${a.id}`}
          className={`inline-flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded-full border ${
            a.routing_eligible
              ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
              : "bg-amber-500/10 text-amber-400 border-amber-500/20"
          }`}
        >
          {a.routing_eligible ? <ShieldCheck className="h-3 w-3" /> : <ShieldOff className="h-3 w-3" />}
          {a.routing_eligible ? "ELIGIBLE" : "INELIGIBLE"}
        </span>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 font-mono text-[11px]">
        <span className="text-muted-foreground">Status</span>
        <span data-testid={`health-status-${a.id}`}>{(a.health_status || "").toUpperCase()}</span>
        <span className="text-muted-foreground">State</span>
        <span className={a.enabled ? "text-emerald-400" : "text-red-400"}>{a.enabled ? "ENABLED" : "DISABLED"}</span>
        <span className="text-muted-foreground">Priority</span>
        <span>{a.priority}</span>
        <span className="text-muted-foreground">Success rate</span>
        <span>{rate} <span className="text-muted-foreground">({m.succeeded}/{m.total})</span></span>
        <span className="text-muted-foreground">Failed</span>
        <span>{m.failed}</span>
        <span className="text-muted-foreground">Last payment</span>
        <span>{m.last_payment_at ? new Date(m.last_payment_at).toLocaleString() : "—"}</span>
        <span className="text-muted-foreground">Credentials</span>
        <span>{a.has_credentials ? "set" : "none"}</span>
        <span className="text-muted-foreground">Checked</span>
        <span>{a.checked_at ? new Date(a.checked_at).toLocaleTimeString() : "—"}</span>
      </div>

      {(a.recent_errors || []).length > 0 && (
        <div className="mt-3" data-testid={`health-errors-${a.id}`}>
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1">Recent errors</p>
          <ul className="space-y-1">
            {a.recent_errors.map((e, i) => (
              <li key={i} className="flex items-center justify-between font-mono text-[11px] text-red-400">
                <span>{e.reference}: {e.error}</span>
                <span className="text-muted-foreground">{new Date(e.at).toLocaleTimeString()}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

function EnvSection({ env, data }) {
  const accounts = data?.accounts || [];
  const failovers = data?.recent_failovers || [];
  const isLive = env === "live";
  return (
    <div className="mb-8" data-testid={`health-env-${env}`}>
      <div className="flex items-center gap-2 mb-3">
        <h3 className="font-heading text-lg font-medium">{isLive ? "Live / Production" : "Sandbox / Test"}</h3>
        <span className={`text-[11px] font-mono px-2 py-0.5 rounded-full border ${
          isLive ? "bg-red-500/10 text-red-400 border-red-500/20" : "bg-sky-500/10 text-sky-400 border-sky-500/20"
        }`}>{env.toUpperCase()}</span>
      </div>
      {accounts.length === 0 ? (
        <EmptyState message={`No ${env} provider accounts configured.`} testid={`health-empty-${env}`} />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-6">
          {accounts.map((a) => <AccountCard key={a.id} a={a} />)}
        </div>
      )}

      {failovers.length > 0 && (
        <div className="mt-4" data-testid={`health-failovers-${env}`}>
          <div className="flex items-center gap-2 mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            <GitBranch className="h-3.5 w-3.5" /> Recent failover activity
          </div>
          <div className="space-y-1.5">
            {failovers.map((f, i) => (
              <div key={i} data-testid={`health-failover-${env}-${i}`}
                className="rounded border border-border bg-secondary/40 px-3 py-2 font-mono text-[11px]">
                <div className="flex items-center justify-between">
                  <span className="font-semibold">{f.reference}</span>
                  <span className="text-muted-foreground">{new Date(f.at).toLocaleString()}</span>
                </div>
                <div className="flex flex-wrap items-center gap-2 mt-1">
                  {f.attempts.map((at, j) => (
                    <span key={j} className="inline-flex items-center gap-1">
                      {at.success ? <CheckCircle2 className="h-3 w-3 text-emerald-500" /> : <XCircle className="h-3 w-3 text-destructive" />}
                      {at.provider_key}
                      {j < f.attempts.length - 1 && <span className="text-muted-foreground">→</span>}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function ProviderHealth() {
  const { selectedTenantId } = useAuth();
  const [data, setData] = useState(null);

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const { data } = await api.get("/providers/health-board", { params: { tenant_id: selectedTenantId } });
    setData(data);
  }, [selectedTenantId]);

  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);

  const envs = data?.environments || {};

  return (
    <div data-testid="provider-health-page">
      <PageHeader
        title="Provider Health"
        subtitle="Live provider account health, routing eligibility, metrics and failover activity."
        action={<span className="flex items-center gap-1.5 text-xs font-mono text-muted-foreground"><Activity className="h-3.5 w-3.5" /> auto-refresh 15s</span>}
      />
      <EnvSection env="sandbox" data={envs.sandbox} />
      <EnvSection env="live" data={envs.live} />
    </div>
  );
}
