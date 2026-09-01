import { useEffect, useState, useCallback } from "react";
import { Activity, GitBranch, CheckCircle2, XCircle, ShieldCheck, ShieldOff, BellRing, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, EmptyState } from "@/components/common";

function HealthDot({ status }) {
  const up = status === "up";
  return <span className={`h-2 w-2 rounded-full ${up ? "bg-emerald-400 animate-pulse" : "bg-red-400"}`} />;
}

function AccountCard({ a, alerting }) {
  const m = a.metrics || {};
  const rate = m.success_rate != null ? `${Math.round(m.success_rate * 100)}%` : "—";
  return (
    <Panel data-testid={`health-account-${a.id}`} className={`cp-anim ${alerting ? "ring-1 ring-amber-500/40" : ""}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <HealthDot status={a.health_status} />
          <div>
            <p className="text-sm font-medium">{a.display_name}</p>
            <p className="font-mono text-[11px] text-muted-foreground">{a.provider_key}</p>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          {alerting && (
            <span data-testid={`health-alert-badge-${a.id}`}
              className="inline-flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded-full border bg-amber-500/10 text-amber-400 border-amber-500/20">
              <BellRing className="h-3 w-3" /> ALERT
            </span>
          )}
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

function EnvSection({ env, data, alertKeys }) {
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
          {accounts.map((a) => <AccountCard key={a.id} a={a} alerting={alertKeys?.has(a.id)} />)}
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
  const [alerts, setAlerts] = useState([]);
  const [checking, setChecking] = useState(false);
  const [thresholds, setThresholds] = useState({ success_rate_threshold: 0.5, min_sample: 5 });
  const [savingCfg, setSavingCfg] = useState(false);

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const [board, al, cfg] = await Promise.all([
      api.get("/providers/health-board", { params: { tenant_id: selectedTenantId } }),
      api.get("/providers/alerts", { params: { tenant_id: selectedTenantId } }),
      api.get("/providers/alerts/settings", { params: { tenant_id: selectedTenantId } }),
    ]);
    setData(board.data);
    setAlerts(al.data);
    setThresholds(cfg.data);
  }, [selectedTenantId]);

  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);

  const saveThresholds = async () => {
    setSavingCfg(true);
    try {
      const { data } = await api.put("/providers/alerts/settings", {
        success_rate_threshold: Number(thresholds.success_rate_threshold),
        min_sample: Number(thresholds.min_sample),
      }, { params: { tenant_id: selectedTenantId } });
      setThresholds(data);
      toast.success("Alert thresholds saved");
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Save failed");
    } finally { setSavingCfg(false); }
  };

  const checkNow = async () => {
    setChecking(true);
    try {
      const { data } = await api.post("/providers/alerts/evaluate", null, { params: { tenant_id: selectedTenantId } });
      const fired = (data.changes || []).filter((c) => c.transition === "alerting").length;
      const recovered = (data.changes || []).filter((c) => c.transition === "recovered").length;
      toast.success(`Evaluated · ${data.active_alerts.length} active` +
        (fired ? ` · ${fired} new` : "") + (recovered ? ` · ${recovered} recovered` : ""));
      load();
    } catch (e) {
      toast.error("Evaluation failed");
    } finally { setChecking(false); }
  };

  const envs = data?.environments || {};
  const alertKeys = new Set(alerts.map((a) => a.provider_account_id));

  return (
    <div data-testid="provider-health-page">
      <PageHeader
        title="Provider Health"
        subtitle="Live provider account health, routing eligibility, metrics and failover activity."
        action={
          <Button size="sm" variant="outline" onClick={checkNow} disabled={checking} data-testid="alerts-check-now">
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${checking ? "animate-spin" : ""}`} /> Check health now
          </Button>
        }
      />

      <Panel className="mb-6" data-testid="alert-thresholds-panel">
        <div className="flex flex-wrap items-end gap-4">
          <div className="space-y-1">
            <Label className="text-xs">Success-rate threshold (%)</Label>
            <Input
              type="number" min="0" max="100" className="w-32"
              data-testid="threshold-success-rate-input"
              value={Math.round((thresholds.success_rate_threshold ?? 0) * 100)}
              onChange={(e) => setThresholds({ ...thresholds, success_rate_threshold: (Number(e.target.value) || 0) / 100 })}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Min sample (payments)</Label>
            <Input
              type="number" min="1" className="w-32"
              data-testid="threshold-min-sample-input"
              value={thresholds.min_sample ?? 5}
              onChange={(e) => setThresholds({ ...thresholds, min_sample: Number(e.target.value) || 1 })}
            />
          </div>
          <Button size="sm" onClick={saveThresholds} disabled={savingCfg} data-testid="threshold-save-button">
            {savingCfg ? "Saving…" : "Save thresholds"}
          </Button>
          <p className="text-[11px] text-muted-foreground font-mono">
            Alert when a provider drops below {Math.round((thresholds.success_rate_threshold ?? 0) * 100)}% success over ≥ {thresholds.min_sample} payments.
          </p>
        </div>
      </Panel>

      {alerts.length > 0 && (
        <div className="mb-6 rounded-lg border border-amber-500/30 bg-amber-500/10 p-4" data-testid="alerts-banner">
          <div className="flex items-center gap-2 mb-2 text-amber-400 text-sm font-medium">
            <BellRing className="h-4 w-4" /> {alerts.length} active provider alert{alerts.length > 1 ? "s" : ""}
          </div>
          <ul className="space-y-1">
            {alerts.map((a) => (
              <li key={a.provider_account_id} data-testid={`alert-${a.provider_account_id}`}
                className="flex items-center justify-between font-mono text-[11px]">
                <span>
                  <span className={`px-1.5 py-0.5 rounded mr-2 ${a.severity === "critical" ? "bg-red-500/20 text-red-300" : "bg-amber-500/20 text-amber-300"}`}>
                    {(a.severity || "").toUpperCase()}
                  </span>
                  <span className="font-semibold">{a.provider_key}</span> · {a.environment} — {a.reason}
                </span>
                <span className="text-muted-foreground">{a.since ? new Date(a.since).toLocaleTimeString() : ""}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <EnvSection env="sandbox" data={envs.sandbox} alertKeys={alertKeys} />
      <EnvSection env="live" data={envs.live} alertKeys={alertKeys} />
    </div>
  );
}
