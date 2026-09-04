import { useEffect, useState, useCallback } from "react";
import {
  Plus, Plug, Check, ChevronLeft, ChevronRight, ShieldCheck, KeyRound,
  Layers, Wallet, Activity, ClipboardList, Loader2, CircleCheck, CircleX, Globe,
} from "lucide-react";
import { toast } from "sonner";
import { api, formatApiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, StatusBadge, EmptyState } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";

const STEPS = [
  { key: "provider", label: "Select Provider", icon: Plug },
  { key: "environment", label: "Environment", icon: Globe },
  { key: "credentials", label: "Credentials", icon: KeyRound },
  { key: "capabilities", label: "Capabilities", icon: Layers },
  { key: "acceptance", label: "Acceptance Mapping", icon: Wallet },
  { key: "test", label: "Test Connection", icon: Activity },
  { key: "review", label: "Review & Save", icon: ClipboardList },
];

const EMPTY_FORM = {
  provider_key: "", display_name: "", mode: "", priority: 10, credentials: {},
  supported_currencies: [], supported_countries: [], payment_methods: [], supported_flows: [],
};

function Chip({ active, onClick, children, testid }) {
  return (
    <button
      type="button"
      data-testid={testid}
      onClick={onClick}
      className={`text-xs font-mono px-2.5 py-1 rounded-md border transition-colors ${
        active
          ? "bg-primary/20 border-primary/60 text-primary"
          : "bg-secondary/40 border-border text-muted-foreground hover:border-primary/40"
      }`}
    >
      {children}
    </button>
  );
}

export default function Providers() {
  const { selectedTenantId } = useAuth();
  const [available, setAvailable] = useState([]);
  const [configured, setConfigured] = useState([]);
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [form, setForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState(null);
  const [healthBusy, setHealthBusy] = useState(false);
  const [acceptance, setAcceptance] = useState([]);

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const [a, c] = await Promise.all([
      api.get("/providers/available"),
      api.get("/providers", { params: { tenant_id: selectedTenantId } }),
    ]);
    setAvailable(a.data);
    setConfigured(c.data);
  }, [selectedTenantId]);
  useEffect(() => { load(); }, [load]);

  const meta = available.find((p) => p.key === form.provider_key);
  const isUpi = (meta?.payment_methods || []).includes("upi");

  const openWizard = () => {
    setForm(EMPTY_FORM);
    setStep(0);
    setHealth(null);
    setAcceptance([]);
    setOpen(true);
  };

  const pickProvider = (p) => {
    setForm({
      ...EMPTY_FORM,
      provider_key: p.key,
      display_name: "",
      mode: (p.supported_environments || ["sandbox"])[0] || "sandbox",
    });
    setHealth(null);
  };

  const toggle = (field, value) => {
    setForm((f) => {
      const arr = f[field] || [];
      return { ...f, [field]: arr.includes(value) ? arr.filter((x) => x !== value) : [...arr, value] };
    });
  };

  const loadAcceptance = useCallback(async () => {
    if (!selectedTenantId || !form.mode) return;
    try {
      const res = await api.get("/payment-acceptance/accounts", {
        params: { tenant_id: selectedTenantId, environment: form.mode },
      });
      setAcceptance(res.data);
    } catch (e) {
      setAcceptance([]);
    }
  }, [selectedTenantId, form.mode]);

  const runHealth = async () => {
    setHealthBusy(true);
    setHealth(null);
    try {
      const res = await api.get(`/providers/${form.provider_key}/health`, {
        params: { environment: form.mode },
      });
      setHealth(res.data);
    } catch (e) {
      setHealth({ status: "error", detail: formatApiError(e.response?.data?.detail) });
    } finally {
      setHealthBusy(false);
    }
  };

  // load acceptance accounts when entering the acceptance step
  useEffect(() => {
    if (open && STEPS[step].key === "acceptance" && isUpi) loadAcceptance();
  }, [open, step, isUpi, loadAcceptance]);

  const requiredCreds = (meta?.required_credentials || []).filter((c) => c.required);
  const credsFilled = requiredCreds.every((c) => (form.credentials[c.key] || "").trim().length > 0);

  const canNext = () => {
    switch (STEPS[step].key) {
      case "provider": return !!form.provider_key;
      case "environment": return !!form.mode;
      case "credentials": return credsFilled;
      default: return true;
    }
  };

  const save = async () => {
    setBusy(true);
    try {
      const hasCredInputs = (meta?.required_credentials || []).length > 0;
      const creds = hasCredInputs
        ? Object.fromEntries(Object.entries(form.credentials).filter(([, v]) => (v || "").trim()))
        : null;
      await api.post("/providers", {
        provider_key: form.provider_key,
        display_name: form.display_name || meta?.display_name || form.provider_key,
        mode: form.mode,
        enabled: true,
        priority: Number(form.priority) || 100,
        supported_currencies: form.supported_currencies,
        supported_countries: form.supported_countries,
        supported_methods: form.payment_methods,
        supported_flows: form.supported_flows,
        credentials: creds && Object.keys(creds).length ? creds : null,
      }, { params: { tenant_id: selectedTenantId } });
      toast.success("Provider account connected");
      setOpen(false);
      load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const toggleEnabled = async (p) => {
    try {
      await api.patch(`/providers/${p.id}`, { enabled: !p.enabled });
      toast.success(p.enabled ? "Account disabled" : "Account enabled");
      load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };

  return (
    <div data-testid="providers-page">
      <PageHeader
        title="Provider Adapters"
        subtitle="Connect payment providers with a guided wizard. Sandbox and live environments are both supported per plugin."
        action={
          <Button data-testid="connect-provider-button" onClick={openWizard}>
            <Plus className="h-4 w-4 mr-2" /> Connect Provider
          </Button>
        }
      />

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-3xl" data-testid="provider-wizard-dialog">
          <DialogHeader>
            <DialogTitle>Connect a Payment Provider</DialogTitle>
            <DialogDescription>Guided setup — configure environment, credentials, capabilities and test before saving.</DialogDescription>
          </DialogHeader>

          <div className="grid grid-cols-1 md:grid-cols-[200px_1fr] gap-6">
            {/* Step rail */}
            <ol className="hidden md:flex flex-col gap-1" data-testid="wizard-steps">
              {STEPS.map((s, i) => {
                const Icon = s.icon;
                const done = i < step;
                const active = i === step;
                return (
                  <li key={s.key}>
                    <div className={`flex items-center gap-2.5 px-2.5 py-2 rounded-md text-sm ${
                      active ? "bg-primary/15 text-primary" : done ? "text-foreground" : "text-muted-foreground"
                    }`}>
                      <span className={`h-6 w-6 shrink-0 rounded-full flex items-center justify-center border ${
                        active ? "border-primary bg-primary/20" : done ? "border-primary/50 bg-primary/10" : "border-border"
                      }`}>
                        {done ? <Check className="h-3.5 w-3.5" /> : <Icon className="h-3.5 w-3.5" />}
                      </span>
                      <span className="leading-tight">{s.label}</span>
                    </div>
                  </li>
                );
              })}
            </ol>

            {/* Step content */}
            <div className="min-h-[320px] flex flex-col" data-testid={`wizard-step-${STEPS[step].key}`}>
              <div className="flex-1 space-y-4">
                {/* STEP: provider */}
                {STEPS[step].key === "provider" && (
                  <div className="space-y-3">
                    <p className="text-sm text-muted-foreground">Choose a provider plugin to connect.</p>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {available.map((p) => {
                        const sel = form.provider_key === p.key;
                        return (
                          <button
                            key={p.key}
                            type="button"
                            data-testid={`wizard-provider-${p.key}`}
                            onClick={() => pickProvider(p)}
                            className={`text-left p-3 rounded-lg border transition-colors ${
                              sel ? "border-primary bg-primary/10" : "border-border hover:border-primary/40"
                            }`}
                          >
                            <div className="flex items-center justify-between">
                              <span className="font-medium">{p.display_name}</span>
                              {sel && <CircleCheck className="h-4 w-4 text-primary" />}
                            </div>
                            <p className="text-xs font-mono text-muted-foreground mt-0.5">{p.key}</p>
                            <div className="mt-2 flex flex-wrap gap-1">
                              {(p.supported_environments || []).map((e) => (
                                <span key={e} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-secondary/60 border border-border">{e}</span>
                              ))}
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* STEP: environment */}
                {STEPS[step].key === "environment" && meta && (
                  <div className="space-y-3">
                    <p className="text-sm text-muted-foreground">Select the environment for this provider account.</p>
                    <div className="grid grid-cols-2 gap-3">
                      {(meta.supported_environments || ["sandbox"]).map((env) => {
                        const sel = form.mode === env;
                        return (
                          <button
                            key={env}
                            type="button"
                            data-testid={`wizard-environment-${env}`}
                            onClick={() => { setForm({ ...form, mode: env }); setHealth(null); }}
                            className={`p-4 rounded-lg border text-left transition-colors ${
                              sel ? "border-primary bg-primary/10" : "border-border hover:border-primary/40"
                            }`}
                          >
                            <div className="flex items-center justify-between">
                              <span className="font-medium capitalize">{env}</span>
                              {sel && <CircleCheck className="h-4 w-4 text-primary" />}
                            </div>
                            <p className="text-xs text-muted-foreground mt-1">
                              {env === "live" ? "Real-money processing" : "Test / non-production"}
                            </p>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* STEP: credentials */}
                {STEPS[step].key === "credentials" && meta && (
                  <div className="space-y-3">
                    {(meta.required_credentials || []).length === 0 ? (
                      <div className="flex items-center gap-2 text-sm text-muted-foreground p-3 rounded-lg border border-border bg-secondary/30" data-testid="wizard-no-credentials">
                        <ShieldCheck className="h-4 w-4 text-primary" />
                        This provider requires no credentials in {form.mode} mode.
                      </div>
                    ) : (
                      <>
                        <p className="text-sm text-muted-foreground flex items-center gap-1.5">
                          <ShieldCheck className="h-4 w-4 text-primary" /> Credentials are encrypted at rest and never displayed again.
                        </p>
                        {(meta.required_credentials || []).map((c) => (
                          <div key={c.key} className="space-y-1.5">
                            <Label>{c.label}{c.required ? " *" : ""}</Label>
                            <Input
                              data-testid={`wizard-credential-${c.key}`}
                              type={c.secret ? "password" : "text"}
                              placeholder={c.label}
                              value={form.credentials[c.key] || ""}
                              onChange={(e) => setForm({ ...form, credentials: { ...form.credentials, [c.key]: e.target.value } })}
                            />
                          </div>
                        ))}
                      </>
                    )}
                  </div>
                )}

                {/* STEP: capabilities */}
                {STEPS[step].key === "capabilities" && meta && (
                  <div className="space-y-4">
                    <p className="text-sm text-muted-foreground">Optionally narrow capabilities. Leave empty to inherit all of the plugin's supported values.</p>
                    <div className="space-y-2">
                      <Label className="text-xs uppercase tracking-wide text-muted-foreground">Currencies</Label>
                      <div className="flex flex-wrap gap-1.5">
                        {(meta.supported_currencies || []).map((c) => (
                          <Chip key={c} testid={`wizard-currency-${c}`} active={form.supported_currencies.includes(c)} onClick={() => toggle("supported_currencies", c)}>{c}</Chip>
                        ))}
                      </div>
                    </div>
                    <div className="space-y-2">
                      <Label className="text-xs uppercase tracking-wide text-muted-foreground">Countries</Label>
                      <div className="flex flex-wrap gap-1.5">
                        {(meta.supported_countries || []).length === 0
                          ? <span className="text-xs text-muted-foreground">Unrestricted</span>
                          : (meta.supported_countries || []).map((c) => (
                            <Chip key={c} testid={`wizard-country-${c}`} active={form.supported_countries.includes(c)} onClick={() => toggle("supported_countries", c)}>{c}</Chip>
                          ))}
                      </div>
                    </div>
                    <div className="space-y-2">
                      <Label className="text-xs uppercase tracking-wide text-muted-foreground">Payment methods</Label>
                      <div className="flex flex-wrap gap-1.5">
                        {(meta.payment_methods || []).map((c) => (
                          <Chip key={c} testid={`wizard-method-${c}`} active={form.payment_methods.includes(c)} onClick={() => toggle("payment_methods", c)}>{c}</Chip>
                        ))}
                      </div>
                    </div>
                    <div className="space-y-2">
                      <Label className="text-xs uppercase tracking-wide text-muted-foreground">Flows</Label>
                      <div className="flex flex-wrap gap-1.5">
                        {(meta.supported_flows || []).map((c) => (
                          <Chip key={c} testid={`wizard-flow-${c}`} active={form.supported_flows.includes(c)} onClick={() => toggle("supported_flows", c)}>{c}</Chip>
                        ))}
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-3 pt-1">
                      <div className="space-y-1.5">
                        <Label>Display name</Label>
                        <Input data-testid="wizard-display-name" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder={meta.display_name} />
                      </div>
                      <div className="space-y-1.5">
                        <Label>Priority</Label>
                        <Input data-testid="wizard-priority" type="number" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} />
                      </div>
                    </div>
                  </div>
                )}

                {/* STEP: acceptance */}
                {STEPS[step].key === "acceptance" && (
                  <div className="space-y-3">
                    {!isUpi ? (
                      <div className="flex items-center gap-2 text-sm text-muted-foreground p-3 rounded-lg border border-border bg-secondary/30" data-testid="wizard-acceptance-na">
                        <Wallet className="h-4 w-4" />
                        Acceptance account mapping applies to UPI providers only — not applicable here.
                      </div>
                    ) : (
                      <>
                        <p className="text-sm text-muted-foreground">Eligible UPI acceptance accounts (VPAs) for this tenant in <span className="font-mono">{form.mode}</span>. This view is informational and is not persisted with the provider.</p>
                        {acceptance.length === 0 ? (
                          <EmptyState message="No acceptance accounts configured for this environment." testid="wizard-acceptance-empty" />
                        ) : (
                          <div className="space-y-2" data-testid="wizard-acceptance-list">
                            {acceptance.map((a) => (
                              <div key={a.id} className="flex items-center justify-between p-3 rounded-lg border border-border bg-secondary/20" data-testid={`wizard-acceptance-${a.id}`}>
                                <div>
                                  <p className="text-sm font-medium">{a.display_name}</p>
                                  <p className="text-xs font-mono text-muted-foreground">{a.upi_vpa} · {a.currency} · {a.country}</p>
                                </div>
                                <div className="flex items-center gap-2 text-xs">
                                  <span className="font-mono text-muted-foreground">P{a.priority}</span>
                                  <StatusBadge status={a.verification_status === "verified" ? "active" : "pending"} />
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}

                {/* STEP: test */}
                {STEPS[step].key === "test" && (
                  <div className="space-y-4">
                    <p className="text-sm text-muted-foreground">Run a live health check against the plugin in <span className="font-mono">{form.mode}</span> before saving.</p>
                    <Button variant="outline" data-testid="wizard-run-health" onClick={runHealth} disabled={healthBusy}>
                      {healthBusy ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Activity className="h-4 w-4 mr-2" />}
                      Test connection
                    </Button>
                    {health && (
                      <div
                        data-testid="wizard-health-result"
                        className={`p-3 rounded-lg border flex items-start gap-2.5 text-sm ${
                          health.status === "up"
                            ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-400"
                            : "border-red-500/40 bg-red-500/10 text-red-400"
                        }`}
                      >
                        {health.status === "up" ? <CircleCheck className="h-4 w-4 mt-0.5" /> : <CircleX className="h-4 w-4 mt-0.5" />}
                        <div>
                          <p className="font-medium">
                            {health.status === "up" ? "Healthy — connection OK" : `Not healthy: ${health.status}`}
                          </p>
                          <p className="text-xs font-mono opacity-80 mt-0.5">
                            {health.detail || `mode: ${health.mode || form.mode}${health.test_mode ? " · test_mode" : ""}`}
                          </p>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* STEP: review */}
                {STEPS[step].key === "review" && meta && (
                  <div className="space-y-3" data-testid="wizard-review">
                    <p className="text-sm text-muted-foreground">Review and save this provider account.</p>
                    <dl className="text-sm rounded-lg border border-border divide-y divide-border">
                      {[
                        ["Provider", `${meta.display_name} (${meta.key})`],
                        ["Environment", form.mode],
                        ["Display name", form.display_name || meta.display_name],
                        ["Priority", String(form.priority)],
                        ["Credentials", (meta.required_credentials || []).length === 0 ? "none required" : (credsFilled ? "provided (encrypted)" : "missing")],
                        ["Currencies", form.supported_currencies.length ? form.supported_currencies.join(", ") : "inherit all"],
                        ["Countries", form.supported_countries.length ? form.supported_countries.join(", ") : "inherit all"],
                        ["Methods", form.payment_methods.length ? form.payment_methods.join(", ") : "inherit all"],
                        ["Flows", form.supported_flows.length ? form.supported_flows.join(", ") : "inherit all"],
                        ["Health check", health ? (health.status === "up" ? "healthy" : health.status) : "not run"],
                      ].map(([k, v]) => (
                        <div key={k} className="flex items-start justify-between px-3 py-2 gap-4">
                          <dt className="text-muted-foreground">{k}</dt>
                          <dd className="font-mono text-right">{v}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                )}
              </div>
            </div>
          </div>

          <DialogFooter className="flex items-center justify-between sm:justify-between">
            <Button
              variant="ghost"
              data-testid="wizard-back"
              onClick={() => setStep((s) => Math.max(0, s - 1))}
              disabled={step === 0}
            >
              <ChevronLeft className="h-4 w-4 mr-1" /> Back
            </Button>
            {STEPS[step].key === "review" ? (
              <Button data-testid="wizard-save" onClick={save} disabled={busy || !credsFilled}>
                {busy ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Check className="h-4 w-4 mr-2" />}
                Save Provider
              </Button>
            ) : (
              <Button
                data-testid="wizard-next"
                onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))}
                disabled={!canNext()}
              >
                Next <ChevronRight className="h-4 w-4 ml-1" />
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {configured.length === 0 ? (
        <Panel><EmptyState message="No providers configured for this tenant." testid="providers-empty" /></Panel>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-6">
          {configured.map((p) => (
            <Panel key={p.id} className="cp-anim">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div className="h-10 w-10 rounded-lg bg-primary/15 text-primary flex items-center justify-center"><Plug className="h-5 w-5" /></div>
                  <div>
                    <p className="font-medium">{p.display_name}</p>
                    <p className="text-xs font-mono text-muted-foreground">{p.provider_key}</p>
                  </div>
                </div>
                <StatusBadge status={p.enabled ? "active" : "suspended"} />
              </div>
              <div className="mt-4 flex items-center justify-between text-xs font-mono text-muted-foreground">
                <span>ENV: {p.mode.toUpperCase()}</span>
                <span>PRIORITY: {p.priority}</span>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(p.supported_currencies || []).map((c) => (
                  <span key={c} className="text-xs font-mono px-2 py-0.5 rounded bg-secondary/60 border border-border">{c}</span>
                ))}
              </div>
              <div className="mt-3 flex items-center justify-between text-xs">
                <span className="font-mono text-muted-foreground" data-testid={`provider-credentials-status-${p.id}`}>
                  {p.credentials_ref ? "CREDENTIALS: set" : "CREDENTIALS: none"}
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  data-testid={`provider-toggle-${p.id}`}
                  onClick={() => toggleEnabled(p)}
                >
                  {p.enabled ? "Disable" : "Enable"}
                </Button>
              </div>
            </Panel>
          ))}
        </div>
      )}
    </div>
  );
}
