import { useEffect, useState, useCallback } from "react";
import { Plus, Landmark, Pencil, Trash2, ShieldCheck, History, Download, Star, Check, X } from "lucide-react";
import { toast } from "sonner";
import { api, formatApiError, downloadCsv } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, StatusBadge, EmptyState } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const EMPTY = {
  account_type: "upi", display_name: "", upi_vpa: "", bank_name: "", account_holder_name: "",
  country: "IN", currency: "INR", environment: "sandbox", priority: 1, enabled: true,
};
const CURRENCIES = ["INR", "USD", "GBP", "EUR", "AUD", "CAD", "SGD"];
const COUNTRIES = ["IN", "US", "GB", "SG", "AU", "CA", "AE", "LK"];

export default function PaymentAcceptance() {
  const { selectedTenantId, hasPermission } = useAuth();
  const canManage = hasPermission("payment_acceptance_account.manage");
  const [accounts, setAccounts] = useState([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [auditFor, setAuditFor] = useState(null);
  const [auditRows, setAuditRows] = useState([]);

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const { data } = await api.get("/payment-acceptance/accounts", { params: { tenant_id: selectedTenantId } });
    setAccounts(data);
  }, [selectedTenantId]);
  useEffect(() => { load(); }, [load]);

  const openAdd = () => { setEditing(null); setForm(EMPTY); setOpen(true); };
  const openEdit = (a) => {
    setEditing(a);
    setForm({ account_type: a.account_type, display_name: a.display_name, upi_vpa: a.upi_vpa || "",
      bank_name: a.bank_name || "", account_holder_name: a.account_holder_name || "", country: a.country,
      currency: a.currency, environment: a.environment, priority: a.priority, enabled: a.enabled });
    setOpen(true);
  };

  const save = async () => {
    setBusy(true);
    try {
      const payload = { ...form, priority: Number(form.priority) || 1 };
      if (editing) {
        await api.patch(`/payment-acceptance/accounts/${editing.id}`, payload);
        toast.success("Acceptance account updated");
      } else {
        await api.post("/payment-acceptance/accounts", payload, { params: { tenant_id: selectedTenantId } });
        toast.success("UPI acceptance account added");
      }
      setOpen(false); setForm(EMPTY); setEditing(null); load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const toggle = async (a) => {
    try {
      await api.post(`/payment-acceptance/accounts/${a.id}/${a.enabled ? "disable" : "enable"}`);
      toast.success(a.enabled ? "Account disabled" : "Account enabled");
      load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };

  const setPriority = async (a, priority) => {
    try {
      await api.post(`/payment-acceptance/accounts/${a.id}/priority`, { priority: Number(priority) || 1 });
      load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };

  const remove = async (a) => {
    if (!window.confirm(`Delete acceptance account "${a.display_name}"?`)) return;
    try {
      await api.delete(`/payment-acceptance/accounts/${a.id}`);
      toast.success("Acceptance account archived");
      load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };

  const requestVerification = async (a) => {
    try {
      await api.post(`/payment-acceptance/accounts/${a.id}/request-verification`);
      toast.success("Verification requested — status is now pending");
      load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };

  const openAudit = async (a) => {
    setAuditFor(a);
    try {
      const { data } = await api.get(`/payment-acceptance/accounts/${a.id}/audit`);
      setAuditRows(data);
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); setAuditRows([]); }
  };

  const decide = async (a, status) => {
    try {
      await api.post(`/payment-acceptance/accounts/${a.id}/verification`, { status });
      toast.success(`Account ${status}`);
      load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };

  // Primary = highest-priority ENABLED account per currency (where collections would route).
  const primaryIds = (() => {
    const best = {};
    accounts.forEach((a) => {
      if (!a.enabled) return;
      const cur = best[a.currency];
      if (!cur || a.priority < cur.priority) best[a.currency] = a;
    });
    return new Set(Object.values(best).map((a) => a.id));
  })();

  return (
    <div data-testid="payment-acceptance-page">
      <PageHeader
        title="Payment Acceptance"
        subtitle="Merchant-owned UPI accounts that receive customer payments. Separate from external provider adapters."
        action={(
          <div className="flex items-center gap-2">
            <Button variant="outline" data-testid="export-acceptance-button"
              onClick={() => downloadCsv("/payment-acceptance/accounts/export.csv", { tenant_id: selectedTenantId }, "payment_acceptance_accounts.csv")}>
              <Download className="h-4 w-4 mr-2" /> Export CSV
            </Button>
            {canManage && (
              <Dialog open={open} onOpenChange={setOpen}>
                <DialogTrigger asChild>
                  <Button data-testid="add-acceptance-button" onClick={openAdd}><Plus className="h-4 w-4 mr-2" /> Add UPI Account</Button>
                </DialogTrigger>
                <DialogContent data-testid="acceptance-dialog">
              <DialogHeader>
                <DialogTitle>{editing ? "Edit" : "Add"} UPI Acceptance Account</DialogTitle>
                <DialogDescription>A destination that collects customer payments. Verification stays "unverified" until a real provider verifies it.</DialogDescription>
              </DialogHeader>
              <div className="grid grid-cols-2 gap-4 py-2">
                <div className="space-y-2 col-span-2">
                  <Label>Display Name</Label>
                  <Input data-testid="acceptance-display-name" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder="Yes Bank UPI" />
                </div>
                <div className="space-y-2 col-span-2">
                  <Label>UPI VPA / ID</Label>
                  <Input data-testid="acceptance-vpa" value={form.upi_vpa} onChange={(e) => setForm({ ...form, upi_vpa: e.target.value })} placeholder="merchant@yesbank" />
                </div>
                <div className="space-y-2">
                  <Label>Bank Name</Label>
                  <Input data-testid="acceptance-bank" value={form.bank_name} onChange={(e) => setForm({ ...form, bank_name: e.target.value })} placeholder="Yes Bank" />
                </div>
                <div className="space-y-2">
                  <Label>Account Holder</Label>
                  <Input data-testid="acceptance-holder" value={form.account_holder_name} onChange={(e) => setForm({ ...form, account_holder_name: e.target.value })} placeholder="Merchant Pvt Ltd" />
                </div>
                <div className="space-y-2">
                  <Label>Country</Label>
                  <Select value={form.country} onValueChange={(v) => setForm({ ...form, country: v })}>
                    <SelectTrigger data-testid="acceptance-country"><SelectValue /></SelectTrigger>
                    <SelectContent>{COUNTRIES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Currency</Label>
                  <Select value={form.currency} onValueChange={(v) => setForm({ ...form, currency: v })}>
                    <SelectTrigger data-testid="acceptance-currency"><SelectValue /></SelectTrigger>
                    <SelectContent>{CURRENCIES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Environment</Label>
                  <Select value={form.environment} onValueChange={(v) => setForm({ ...form, environment: v })}>
                    <SelectTrigger data-testid="acceptance-environment"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="sandbox">sandbox</SelectItem>
                      <SelectItem value="live">live</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Priority</Label>
                  <Input data-testid="acceptance-priority" type="number" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} />
                </div>
              </div>
              <DialogFooter>
                <Button data-testid="submit-acceptance-button" onClick={save} disabled={busy || !form.display_name || !form.upi_vpa}>{editing ? "Save" : "Add"}</Button>
              </DialogFooter>
              </DialogContent>
            </Dialog>
            )}
          </div>
        )}
      />

      {accounts.length === 0 ? (
        <Panel><EmptyState message="No UPI acceptance accounts configured for this tenant." testid="acceptance-empty" /></Panel>
      ) : (
        <Panel className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-muted-foreground border-b border-border">
                <th className="py-3 pr-4">Display Name</th>
                <th className="py-3 pr-4">UPI / VPA</th>
                <th className="py-3 pr-4">Bank</th>
                <th className="py-3 pr-4">Cur</th>
                <th className="py-3 pr-4">Env</th>
                <th className="py-3 pr-4">Priority</th>
                <th className="py-3 pr-4">Verification</th>
                <th className="py-3 pr-4">Status</th>
                <th className="py-3 pr-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.id} className="border-b border-border/60" data-testid={`acceptance-row-${a.id}`}>
                  <td className="py-3 pr-4 font-medium">
                    <div className="flex items-center gap-2">
                      <Landmark className="h-4 w-4 text-primary" /> {a.display_name}
                      {primaryIds.has(a.id) && (
                        <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 text-primary border border-primary/20 px-2 py-0.5 text-[10px] font-semibold" data-testid={`acceptance-primary-${a.id}`}>
                          <Star className="h-3 w-3" /> Primary
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="py-3 pr-4 font-mono text-xs" data-testid={`acceptance-vpa-${a.id}`}>{a.upi_vpa}</td>
                  <td className="py-3 pr-4">{a.bank_name || "—"}</td>
                  <td className="py-3 pr-4 font-mono">{a.currency}</td>
                  <td className="py-3 pr-4 font-mono uppercase">{a.environment}</td>
                  <td className="py-3 pr-4">
                    {canManage ? (
                      <Input className="h-7 w-16 text-xs" type="number" defaultValue={a.priority}
                        data-testid={`acceptance-priority-${a.id}`}
                        onBlur={(e) => Number(e.target.value) !== a.priority && setPriority(a, e.target.value)} />
                    ) : a.priority}
                  </td>
                  <td className="py-3 pr-4"><StatusBadge status={a.verification_status} /></td>
                  <td className="py-3 pr-4"><StatusBadge status={a.enabled ? "active" : "suspended"} /></td>
                  <td className="py-3 pr-4">
                    <div className="flex items-center justify-end gap-1.5">
                      {canManage && (
                        <>
                          <Button size="sm" variant="outline" className="h-7" data-testid={`acceptance-toggle-${a.id}`} onClick={() => toggle(a)}>{a.enabled ? "Disable" : "Enable"}</Button>
                          {a.verification_status === "unverified" && (
                            <Button size="sm" variant="outline" className="h-7" data-testid={`acceptance-verify-${a.id}`} onClick={() => requestVerification(a)}><ShieldCheck className="h-3.5 w-3.5 mr-1" /> Verify</Button>
                          )}
                          {a.verification_status === "pending" && (
                            <>
                              <Button size="sm" variant="outline" className="h-7 text-emerald-500" data-testid={`acceptance-approve-${a.id}`} onClick={() => decide(a, "verified")}><Check className="h-3.5 w-3.5 mr-1" /> Mark Verified</Button>
                              <Button size="sm" variant="outline" className="h-7 text-destructive" data-testid={`acceptance-reject-${a.id}`} onClick={() => decide(a, "rejected")}><X className="h-3.5 w-3.5 mr-1" /> Reject</Button>
                            </>
                          )}
                          <Button size="icon" variant="ghost" className="h-7 w-7" data-testid={`acceptance-history-${a.id}`} onClick={() => openAudit(a)}><History className="h-3.5 w-3.5" /></Button>
                          <Button size="icon" variant="ghost" className="h-7 w-7" data-testid={`acceptance-edit-${a.id}`} onClick={() => openEdit(a)}><Pencil className="h-3.5 w-3.5" /></Button>
                          <Button size="icon" variant="ghost" className="h-7 w-7 text-destructive" data-testid={`acceptance-delete-${a.id}`} onClick={() => remove(a)}><Trash2 className="h-3.5 w-3.5" /></Button>
                        </>
                      )}
                      {!canManage && (
                        <Button size="icon" variant="ghost" className="h-7 w-7" data-testid={`acceptance-history-${a.id}`} onClick={() => openAudit(a)}><History className="h-3.5 w-3.5" /></Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      <Dialog open={!!auditFor} onOpenChange={(v) => { if (!v) { setAuditFor(null); setAuditRows([]); } }}>
        <DialogContent data-testid="acceptance-audit-dialog">
          <DialogHeader>
            <DialogTitle>Activity — {auditFor?.display_name}</DialogTitle>
            <DialogDescription>Create / update / enable / disable / priority / verification events for this account.</DialogDescription>
          </DialogHeader>
          <div className="max-h-96 overflow-y-auto space-y-2">
            {auditRows.length === 0 ? (
              <p className="text-sm text-muted-foreground" data-testid="acceptance-audit-empty">No activity recorded yet.</p>
            ) : auditRows.map((r) => (
              <div key={r.id} className="rounded border border-border bg-secondary/30 px-3 py-2 text-xs" data-testid={`acceptance-audit-row-${r.id}`}>
                <div className="flex items-center justify-between">
                  <span className="font-medium">{r.action.replace("payment_acceptance_account.", "")}</span>
                  <span className="text-muted-foreground">{new Date(r.created_at).toLocaleString()}</span>
                </div>
                <div className="text-muted-foreground mt-1">{r.actor_email}</div>
                {r.changes && <pre className="mt-1 whitespace-pre-wrap break-all text-[11px] text-muted-foreground">{JSON.stringify(r.changes)}</pre>}
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
