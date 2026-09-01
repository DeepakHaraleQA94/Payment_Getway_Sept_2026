import { useEffect, useState, useCallback } from "react";
import { Plus, Undo2, Download } from "lucide-react";
import { toast } from "sonner";
import { api, money, formatApiError, downloadCsv } from "@/lib/api";
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

export default function Payments() {
  const { selectedTenantId } = useAuth();
  const [payments, setPayments] = useState([]);
  const [providers, setProviders] = useState([]);
  const [open, setOpen] = useState(false);
  const [refundFor, setRefundFor] = useState(null);
  const [form, setForm] = useState({ reference: "", amount: "", currency: "USD", customer_email: "", provider_key: "mock" });
  const [refundAmount, setRefundAmount] = useState("");
  const [busy, setBusy] = useState(false);

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
  }, [selectedTenantId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { load(); }, [load]);

  const createPayment = async () => {
    setBusy(true);
    try {
      await api.post("/payments", {
        reference: form.reference || `ORD-${Date.now()}`,
        amount_minor: Math.round(parseFloat(form.amount) * 100),
        currency: form.currency,
        provider_key: form.provider_key,
        customer_email: form.customer_email || null,
        idempotency_key: `ui-${Date.now()}`,
      }, { params: { tenant_id: selectedTenantId } });
      toast.success("Payment processed via provider plugin");
      setOpen(false);
      setForm({ reference: "", amount: "", currency: "USD", customer_email: "", provider_key: form.provider_key });
      load();
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail));
    } finally { setBusy(false); }
  };

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
                      {providers.map((p) => (
                        <SelectItem key={p.key} value={p.key} data-testid={`payment-provider-option-${p.key}`}>
                          {p.display_name} · {p.mode}
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
                    <Input data-testid="payment-amount-input" type="number" step="0.01" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} placeholder="100.00" />
                  </div>
                  <div className="space-y-2">
                    <Label>Currency</Label>
                    <Input data-testid="payment-currency-input" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value.toUpperCase() })} />
                  </div>
                </div>
                <div className="space-y-2">
                  <Label>Customer email</Label>
                  <Input data-testid="payment-email-input" value={form.customer_email} onChange={(e) => setForm({ ...form, customer_email: e.target.value })} placeholder="buyer@example.com" />
                </div>
                <p className="text-xs font-mono text-muted-foreground">Tip: amounts ending in .13 simulate a sandbox decline.</p>
              </div>
              <DialogFooter>
                <Button data-testid="submit-payment-button" onClick={createPayment} disabled={busy || !form.amount}>Process</Button>
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
    </div>
  );
}
