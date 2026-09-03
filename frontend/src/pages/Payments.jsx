import { useEffect, useState, useCallback, useMemo } from "react";
import { Plus, Undo2, Download, Info, GitBranch, CheckCircle2, XCircle, Mail, ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { api, money, formatApiError, downloadCsv, toMinorUnits, currencySymbol, currencyDecimals } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, StatusBadge, EmptyState } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";

// Readable names for currency codes (display only; server capability stays authoritative).
const CURRENCY_NAMES = {
  INR: "Indian Rupee", USD: "US Dollar", GBP: "British Pound", EUR: "Euro",
  AUD: "Australian Dollar", CAD: "Canadian Dollar", SGD: "Singapore Dollar",
  AED: "UAE Dirham", JPY: "Japanese Yen", LKR: "Sri Lankan Rupee",
};
const currencyLabel = (c) => (CURRENCY_NAMES[c] ? `${c} — ${CURRENCY_NAMES[c]}` : c);

// Display-only thousands grouping for the amount field. Keeps the raw numeric string exact for the
// backend (no floating-point math here); only formats what the operator sees. `decimals`=0 hides the
// fractional part entirely (zero-decimal currencies like JPY).
const groupAmount = (raw, decimals) => {
  if (raw === "" || raw == null) return "";
  const s = String(raw);
  const [intPart, ...rest] = s.split(".");
  const grouped = (intPart || "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  if (decimals === 0) return grouped;
  if (!s.includes(".")) return grouped;
  return `${grouped}.${(rest.join("") || "").slice(0, decimals)}`;
};



export default function Payments() {
  const { selectedTenantId, hasPermission } = useAuth();
  const canCapture = hasPermission("payment.capture");
  const canVoid = hasPermission("payment.void");
  const canResend = hasPermission("payment.create");
  const [payments, setPayments] = useState([]);
  const [providers, setProviders] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [open, setOpen] = useState(false);
  const [refundFor, setRefundFor] = useState(null);
  const [detailFor, setDetailFor] = useState(null);
  const [captureFor, setCaptureFor] = useState(null);
  const [captureAmount, setCaptureAmount] = useState("");
  const [voidFor, setVoidFor] = useState(null);
  const [voidReason, setVoidReason] = useState("");
  const [form, setForm] = useState({ reference: "", amount: "", currency: "", customer_email: "", provider_key: "mock", environment: "sandbox" });
  const [refundAmount, setRefundAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [resendBusy, setResendBusy] = useState(false);

  const resendReceipt = async (p) => {
    setResendBusy(true);
    try {
      const { data } = await api.post(`/payments/${p.id}/receipt/resend`);
      toast.success(`Receipt resent to ${p.customer_email}`);
      setDetailFor(data);
      await load();
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail));
    } finally { setResendBusy(false); }
  };

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const [pay, prov] = await Promise.all([
      api.get("/payments", { params: { tenant_id: selectedTenantId } }),
      api.get("/providers/available"),
    ]);
    setPayments(pay.data);
    setProviders(prov.data);
    if (prov.data.length && !prov.data.some((p) => p.key === form.provider_key)) {
      setForm((f) => ({ ...f, provider_key: prov.data[0].key }));
    }
    // Best-effort per-tenant provider accounts to power provider-aware currency hinting (which
    // currencies the selected provider/account can actually process). Falls back to plugin
    // capability if the caller lacks provider.view. Never blocks the payments list.
    try {
      const accts = await api.get("/providers", { params: { tenant_id: selectedTenantId } });
      setAccounts(accts.data || []);
    } catch { setAccounts([]); }
  }, [selectedTenantId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { load(); }, [load]);

  const createPayment = async () => {
    setBusy(true);
    try {
      await api.post("/payments", {
        reference: form.reference || `ORD-${Date.now()}`,
        amount_minor: toMinorUnits(form.amount, form.currency),
        currency: form.currency,
        provider_key: form.provider_key,
        environment: form.environment,
        customer_email: form.customer_email || null,
        idempotency_key: `ui-${Date.now()}`,
      }, { params: { tenant_id: selectedTenantId } });
      toast.success("Payment processed via provider plugin");
      setOpen(false);
      setForm({ reference: "", amount: "", currency: "", customer_email: "", provider_key: form.provider_key, environment: form.environment });
      load();
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail));
    } finally { setBusy(false); }
  };

  const selectedProvider = providers.find((p) => p.key === form.provider_key);

  // Currency options = the FULL set advertised by the provider capability configuration (union
  // across all registered providers). The user must explicitly pick one; the server remains
  // authoritative and rejects an unsupported currency/provider/country/environment combination.
  const currencyOptions = useMemo(() => {
    const set = new Set();
    providers.forEach((p) => (p.supported_currencies || []).forEach((c) => set.add(c)));
    // Baseline so the selector is never empty before discovery loads.
    ["INR", "USD", "GBP", "EUR", "AUD", "CAD", "SGD", "JPY"].forEach((c) => set.add(c));
    return Array.from(set).sort();
  }, [providers]);

  // Effective currencies a provider can process = its account currencies (when configured) else the
  // plugin's declared capability. Used only for UI hinting; the server stays authoritative.
  const effectiveCurrencies = useCallback((key) => {
    const plugin = providers.find((p) => p.key === key);
    const acct = accounts.find((a) => a.provider_key === key && a.mode === form.environment);
    if (acct && Array.isArray(acct.supported_currencies) && acct.supported_currencies.length) {
      return acct.supported_currencies;
    }
    return plugin?.supported_currencies || [];
  }, [providers, accounts, form.environment]);

  // Is a currency processable by the CURRENT selection? For "auto", any provider suffices.
  const isCurrencySupported = useCallback((code) => {
    if (form.provider_key === "auto") {
      return providers.some((p) => effectiveCurrencies(p.key).includes(code));
    }
    return effectiveCurrencies(form.provider_key).includes(code);
  }, [providers, effectiveCurrencies, form.provider_key]);

  const providerLabel = form.provider_key === "auto"
    ? "the selected providers" : (selectedProvider?.display_name || form.provider_key);
  // UX hint only (server stays authoritative): true when the chosen currency can't be processed
  // by the current provider/account selection (e.g. after switching providers).
  const currencyUnsupported = !!form.currency && !isCurrencySupported(form.currency);

  // Informational: payment methods advertised for the current provider/currency context.
  const methodHint = useMemo(() => {
    if (!form.currency) return [];
    const keys = form.provider_key === "auto" ? providers.map((p) => p.key) : [form.provider_key];
    const set = new Set();
    keys.forEach((k) => {
      if (effectiveCurrencies(k).includes(form.currency)) {
        (providers.find((p) => p.key === k)?.payment_methods || []).forEach((m) => set.add(m));
      }
    });
    return Array.from(set).sort();
  }, [providers, form.provider_key, form.currency, effectiveCurrencies]);


  const submitRefund = async () => {
    setBusy(true);
    try {
      await api.post(`/payments/${refundFor.id}/refunds`, {
        amount_minor: Math.round(parseFloat(refundAmount) * 100),
        reason: "requested_by_customer",
        idempotency_key: `rf-${Date.now()}`,
      });
      toast.success("Refund processed");
      setRefundFor(null);
      setRefundAmount("");
      load();
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail));
    } finally { setBusy(false); }
  };

  const submitCapture = async () => {
    setBusy(true);
    try {
      const body = { idempotency_key: `cap-${Date.now()}` };
      const full = captureFor.amount_minor;
      const minor = Math.round(parseFloat(captureAmount) * 100);
      if (!Number.isNaN(minor) && minor !== full) body.amount_minor = minor; // partial capture
      const { data } = await api.post(`/payments/${captureFor.id}/capture`, body);
      toast.success(`Payment captured — status ${data.status}`);
      setCaptureFor(null); setCaptureAmount("");
      load();
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail));
    } finally { setBusy(false); }
  };

  const submitVoid = async () => {
    setBusy(true);
    try {
      const { data } = await api.post(`/payments/${voidFor.id}/void`, {
        reason: voidReason || null, idempotency_key: `void-${Date.now()}`,
      });
      toast.success(`Payment voided — status ${data.status}`);
      setVoidFor(null); setVoidReason("");
      load();
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail));
    } finally { setBusy(false); }
  };

  return (
    <div data-testid="payments-page">
      <PageHeader
        title="Payments"
        subtitle="Create and inspect transactions processed through the payment engine."
        action={
          <div className="flex gap-2">
            <Button variant="outline" data-testid="export-payments-csv"
              onClick={() => downloadCsv("/reports/export/payments.csv", { tenant_id: selectedTenantId }, "payments.csv")}>
              <Download className="h-4 w-4 mr-2" /> Export CSV
            </Button>
            <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button data-testid="new-payment-button"><Plus className="h-4 w-4 mr-2" /> New Payment</Button>
            </DialogTrigger>
            <DialogContent data-testid="new-payment-dialog">
              <DialogHeader>
                <DialogTitle>Create Payment (Sandbox)</DialogTitle>
                <DialogDescription>Process a sandbox transaction through the mock provider. No real funds move.</DialogDescription>
              </DialogHeader>
              <div className="space-y-4 py-2">
                <div className="space-y-2">
                  <Label>Provider adapter</Label>
                  <Select value={form.provider_key} onValueChange={(v) => setForm({ ...form, provider_key: v })}>
                    <SelectTrigger data-testid="payment-provider-select"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="auto" data-testid="payment-provider-option-auto">
                        Auto — priority routing &amp; failover
                      </SelectItem>
                      {providers.map((p) => (
                        <SelectItem key={p.key} value={p.key} data-testid={`payment-provider-option-${p.key}`}>
                          {p.display_name} · {p.mode}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Environment</Label>
                  <Select value={form.environment} onValueChange={(v) => setForm({ ...form, environment: v })}>
                    <SelectTrigger data-testid="payment-environment-select"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {(selectedProvider?.supported_environments || ["sandbox"]).map((env) => (
                        <SelectItem key={env} value={env} data-testid={`payment-environment-option-${env}`}>
                          {env}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Reference</Label>
                  <Input data-testid="payment-reference-input" value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} placeholder="ORD-1001" />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-2">
                    <Label>Amount</Label>
                    <div className="relative">
                      {form.currency && (
                        <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground font-mono"
                          data-testid="payment-amount-symbol">{currencySymbol(form.currency)}</span>
                      )}
                      <Input data-testid="payment-amount-input" type="text" inputMode="decimal"
                        className={form.currency ? "pl-8" : ""}
                        value={groupAmount(form.amount, currencyDecimals(form.currency))}
                        onChange={(e) => {
                          const d = currencyDecimals(form.currency);
                          let raw = e.target.value.replace(/,/g, "").replace(/[^0-9.]/g, "");
                          const dot = raw.indexOf(".");
                          if (dot !== -1) raw = raw.slice(0, dot + 1) + raw.slice(dot + 1).replace(/\./g, "");
                          if (d === 0) raw = raw.replace(/\./g, "");
                          else if (raw.includes(".")) { const [i, f = ""] = raw.split("."); raw = `${i}.${f.slice(0, d)}`; }
                          setForm({ ...form, amount: raw });
                        }}
                        placeholder={currencyDecimals(form.currency) === 0 ? "100" : "100.00"} />
                    </div>
                  </div>
                  <div className="space-y-2">
                    <Label>Currency</Label>
                    <Select value={form.currency} onValueChange={(v) => setForm({ ...form, currency: v })}>
                      <SelectTrigger data-testid="payment-currency-select">
                        <SelectValue placeholder="Select currency" />
                      </SelectTrigger>
                      <SelectContent>
                        {currencyOptions.map((c) => {
                          const supported = isCurrencySupported(c);
                          return (
                            <SelectItem key={c} value={c} disabled={!supported}
                              data-testid={`payment-currency-option-${c}`}>
                              <span className={supported ? "" : "opacity-50"}>
                                {currencyLabel(c)}{!supported && " · not supported"}
                              </span>
                            </SelectItem>
                          );
                        })}
                      </SelectContent>
                    </Select>
                    {currencyUnsupported && (
                      <p className="text-xs text-destructive" data-testid="currency-unsupported-note">
                        Not supported by {providerLabel}
                      </p>
                    )}
                  </div>
                </div>
                {form.currency && methodHint.length > 0 && (
                  <p className="text-xs text-muted-foreground" data-testid="payment-method-hint">
                    Supported methods for {form.currency} via {providerLabel}: {methodHint.join(", ")}
                  </p>
                )}
                <div className="space-y-2">
                  <Label>Customer email</Label>
                  <Input data-testid="payment-email-input" value={form.customer_email} onChange={(e) => setForm({ ...form, customer_email: e.target.value })} placeholder="buyer@example.com" />
                </div>
                <p className="text-xs font-mono text-muted-foreground">Tip: amounts ending in .13 simulate a sandbox decline.</p>
              </div>
              <DialogFooter>
                <Button data-testid="submit-payment-button" onClick={createPayment} disabled={busy || !form.amount || !form.currency || currencyUnsupported}>Process</Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
          </div>
        }
      />

      <Panel className="p-0 overflow-hidden">
        {payments.length === 0 ? (
          <EmptyState message="No payments yet. Create your first sandbox payment." testid="payments-empty" />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Reference</TableHead>
                  <TableHead>Provider Txn</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead className="text-right">Fee</TableHead>
                  <TableHead className="text-right">Net</TableHead>
                  <TableHead>Risk</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {payments.map((p) => (
                  <TableRow key={p.id} data-testid={`payment-row-${p.reference}`}>
                    <TableCell className="font-mono text-xs">{p.reference}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{p.provider_txn_id || "—"}</TableCell>
                    <TableCell className="text-right font-mono">{money(p.amount_minor, p.currency)}</TableCell>
                    <TableCell className="text-right font-mono text-muted-foreground">{money(p.fee_minor, p.currency)}</TableCell>
                    <TableCell className="text-right font-mono">{money(p.net_minor, p.currency)}</TableCell>
                    <TableCell className="font-mono text-xs">{p.risk_score}</TableCell>
                    <TableCell><StatusBadge status={p.status} /></TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm" data-testid={`details-button-${p.reference}`}
                        onClick={() => setDetailFor(p)}>
                        <Info className="h-3.5 w-3.5 mr-1" /> Details
                      </Button>
                      {p.status === "authorized" && canCapture && (
                        <Button variant="ghost" size="sm" data-testid={`capture-button-${p.reference}`}
                          onClick={() => { setCaptureFor(p); setCaptureAmount((p.amount_minor / 100).toString()); }}>
                          <CheckCircle2 className="h-3.5 w-3.5 mr-1" /> Capture
                        </Button>
                      )}
                      {p.status === "authorized" && canVoid && (
                        <Button variant="ghost" size="sm" data-testid={`void-button-${p.reference}`}
                          onClick={() => { setVoidFor(p); setVoidReason(""); }}>
                          <XCircle className="h-3.5 w-3.5 mr-1" /> Void
                        </Button>
                      )}
                      {["succeeded", "captured", "partially_refunded"].includes(p.status) && (
                        <Button variant="ghost" size="sm" data-testid={`refund-button-${p.reference}`}
                          onClick={() => { setRefundFor(p); setRefundAmount((p.amount_minor / 100).toString()); }}>
                          <Undo2 className="h-3.5 w-3.5 mr-1" /> Refund
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Panel>

      <Dialog open={!!detailFor} onOpenChange={(v) => !v && setDetailFor(null)}>
        <DialogContent data-testid="payment-detail-dialog">
          <DialogHeader>
            <DialogTitle>Payment {detailFor?.reference}</DialogTitle>
            <DialogDescription>Transaction detail and provider routing trace.</DialogDescription>
          </DialogHeader>
          {detailFor && (
            <div className="space-y-4 py-2 text-sm">
              <div className="grid grid-cols-2 gap-x-4 gap-y-2 font-mono text-xs">
                <div className="text-muted-foreground">Status</div>
                <div><StatusBadge status={detailFor.status} /></div>
                <div className="text-muted-foreground">Environment</div>
                <div data-testid="detail-environment">{(detailFor.environment || "sandbox").toUpperCase()}</div>
                <div className="text-muted-foreground">Provider used</div>
                <div data-testid="detail-provider">{detailFor.provider_key}</div>
                <div className="text-muted-foreground">Provider Txn</div>
                <div className="break-all">{detailFor.provider_txn_id || "—"}</div>
                <div className="text-muted-foreground">Amount / Net</div>
                <div>{money(detailFor.amount_minor, detailFor.currency)} / {money(detailFor.net_minor, detailFor.currency)}</div>
              </div>

              <div>
                <div className="flex items-center gap-2 mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  <GitBranch className="h-3.5 w-3.5" /> Routing &amp; Failover Trace
                </div>
                {(detailFor.metadata?.routing_attempts || []).length > 0 ? (
                  <ol className="space-y-1.5" data-testid="routing-trace">
                    {detailFor.metadata.routing_attempts.map((a, i) => (
                      <li key={i} data-testid={`routing-attempt-${i}`}
                        className="flex items-center justify-between rounded border border-border bg-secondary/40 px-3 py-2 font-mono text-xs">
                        <span className="flex items-center gap-2">
                          <span className="text-muted-foreground">#{i + 1}</span>
                          <span className="font-semibold">{a.provider_key}</span>
                          <span className="text-muted-foreground">{a.status}</span>
                          {a.error && <span className="text-destructive">({a.error})</span>}
                        </span>
                        {a.success
                          ? <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                          : <XCircle className="h-4 w-4 text-destructive" />}
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="text-xs text-muted-foreground" data-testid="routing-trace-empty">
                    Routed directly to <span className="font-mono">{detailFor.provider_key}</span> — no failover recorded.
                  </p>
                )}
              </div>

              <div>
                <div className="flex items-center gap-2 mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  <Mail className="h-3.5 w-3.5" /> Customer Receipt
                </div>
                {(() => {
                  const st = detailFor.metadata?.receipt_status;
                  const sentAt = detailFor.metadata?.receipt_sent_at;
                  const tok = detailFor.metadata?.receipt_token;
                  const label = { sent: "Sent", send_failed: "Failed", skipped_no_provider: "Skipped (no email provider)", no_recipient: "No recipient" }[st];
                  const tone = st === "sent" ? "text-emerald-500 border-emerald-500/20 bg-emerald-500/10"
                    : st === "send_failed" ? "text-destructive border-destructive/20 bg-destructive/10"
                    : "text-muted-foreground border-border bg-secondary/40";
                  if (!detailFor.customer_email) {
                    return <p className="text-xs text-muted-foreground" data-testid="receipt-log-none">No customer email — no receipt is sent.</p>;
                  }
                  return (
                    <div className="rounded border border-border bg-secondary/40 px-3 py-2.5 space-y-2" data-testid="receipt-log">
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-mono break-all">{detailFor.customer_email}</span>
                        <span className={`ml-2 shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${tone}`} data-testid="receipt-log-status">
                          {label || (sentAt ? "Sent" : "Not sent yet")}
                        </span>
                      </div>
                      {sentAt && <div className="text-[11px] text-muted-foreground" data-testid="receipt-log-time">Sent {new Date(sentAt).toLocaleString()}</div>}
                      {detailFor.metadata?.receipt_delivery && (() => {
                        const d = detailFor.metadata.receipt_delivery;
                        const dtone = d === "delivered" ? "text-emerald-500 border-emerald-500/20 bg-emerald-500/10"
                          : ["bounced", "complained", "failed"].includes(d) ? "text-destructive border-destructive/20 bg-destructive/10"
                          : "text-muted-foreground border-border bg-secondary/40";
                        return (
                          <div className="flex items-center gap-1.5 text-[11px]">
                            <span className="text-muted-foreground">Delivery:</span>
                            <span className={`rounded-full border px-2 py-0.5 font-semibold capitalize ${dtone}`} data-testid="receipt-log-delivery">{d}</span>
                          </div>
                        );
                      })()}
                      {tok && (
                        <a href={`/receipt/${tok}`} target="_blank" rel="noreferrer"
                          className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline" data-testid="receipt-log-link">
                          <ExternalLink className="h-3 w-3" /> View receipt
                        </a>
                      )}
                      {canResend && (detailFor.status === "succeeded" || detailFor.status === "captured") && (
                        <div className="pt-1">
                          <Button variant="outline" size="sm" className="h-7 text-xs" data-testid="receipt-resend-button"
                            disabled={resendBusy} onClick={() => resendReceipt(detailFor)}>
                            <Mail className="h-3 w-3 mr-1" /> {resendBusy ? "Sending…" : "Resend receipt"}
                          </Button>
                        </div>
                      )}
                    </div>
                  );
                })()}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={!!refundFor} onOpenChange={(v) => !v && setRefundFor(null)}>
        <DialogContent data-testid="refund-dialog">
          <DialogHeader>
            <DialogTitle>Refund {refundFor?.reference}</DialogTitle>
            <DialogDescription>Issue a full or partial refund against this payment.</DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <Label>Refund amount</Label>
            <Input data-testid="refund-amount-input" type="number" step="0.01" value={refundAmount} onChange={(e) => setRefundAmount(e.target.value)} />
            <p className="text-xs text-muted-foreground">Original: {refundFor && money(refundFor.amount_minor, refundFor.currency)}</p>
          </div>
          <DialogFooter>
            <Button data-testid="submit-refund-button" onClick={submitRefund} disabled={busy}>Process Refund</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!captureFor} onOpenChange={(v) => { if (!v && !busy) setCaptureFor(null); }}>
        <DialogContent data-testid="capture-dialog">
          <DialogHeader>
            <DialogTitle>Capture {captureFor?.reference}</DialogTitle>
            <DialogDescription>Capture this authorized payment. Full capture by default; edit the amount for a partial capture.</DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <Label>Capture amount</Label>
            <Input data-testid="capture-amount-input" type="number" step="0.01" value={captureAmount}
              onChange={(e) => setCaptureAmount(e.target.value)} />
            <p className="text-xs text-muted-foreground">Authorized: {captureFor && money(captureFor.amount_minor, captureFor.currency)}</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCaptureFor(null)} disabled={busy} data-testid="capture-cancel">Cancel</Button>
            <Button data-testid="submit-capture-button" onClick={submitCapture} disabled={busy || !captureAmount}>
              {busy ? "Capturing…" : "Capture payment"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!voidFor} onOpenChange={(v) => { if (!v && !busy) setVoidFor(null); }}>
        <DialogContent data-testid="void-dialog">
          <DialogHeader>
            <DialogTitle>Void {voidFor?.reference}</DialogTitle>
            <DialogDescription>Cancel this authorized payment before capture. This releases the authorization and cannot be undone.</DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <Label>Reason (optional)</Label>
            <Input data-testid="void-reason-input" value={voidReason} onChange={(e) => setVoidReason(e.target.value)}
              placeholder="e.g. customer cancelled order" />
            <p className="text-xs text-muted-foreground">Amount: {voidFor && money(voidFor.amount_minor, voidFor.currency)}</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setVoidFor(null)} disabled={busy} data-testid="void-cancel">Cancel</Button>
            <Button variant="destructive" data-testid="submit-void-button" onClick={submitVoid} disabled={busy}>
              {busy ? "Voiding…" : "Void payment"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
