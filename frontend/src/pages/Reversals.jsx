import { useEffect, useState, useCallback } from "react";
import { Undo2, AlertTriangle, ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import { api, money, formatApiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, StatusBadge, EmptyState } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";

// States eligible for a full reversal (mirrors the server-side REVERSIBLE set for display only;
// the backend re-validates eligibility — this is a UI convenience, not business logic).
const REVERSIBLE = new Set(["authorized", "captured", "succeeded"]);

export default function Reversals() {
  const { selectedTenantId, hasPermission } = useAuth();
  const canReverse = hasPermission("payment.reverse");
  const [payments, setPayments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [target, setTarget] = useState(null); // payment selected for reversal
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    setLoading(true);
    try {
      const { data } = await api.get("/payments", { params: { tenant_id: selectedTenantId } });
      setPayments(data);
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail));
    } finally {
      setLoading(false);
    }
  }, [selectedTenantId]);
  useEffect(() => { load(); }, [load]);

  const eligible = payments.filter((p) => REVERSIBLE.has(p.status));
  const reversed = payments.filter((p) => p.status === "reversed");

  const openConfirm = (p) => { setTarget(p); setReason(""); };
  const closeConfirm = () => { if (!submitting) { setTarget(null); setReason(""); } };

  const confirmReverse = async () => {
    if (!target) return;
    setSubmitting(true);
    try {
      const { data } = await api.post(`/payments/${target.id}/reverse`, { reason: reason || null });
      toast.success(`Reversal ${data.status} — ${money(data.amount_minor, data.currency)} unwound`);
      setTarget(null); setReason("");
      load();
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail));
    } finally {
      setSubmitting(false);
    }
  };

  if (!canReverse) {
    return (
      <div data-testid="reversals-page">
        <PageHeader title="Reversal Console" subtitle="Fully unwind an eligible transaction." />
        <Panel className="flex items-center gap-3" data-testid="reversals-no-permission">
          <ShieldAlert className="h-5 w-5 text-amber-400" />
          <p className="text-sm text-muted-foreground">
            You don't have the <span className="font-mono">payment.reverse</span> permission required to reverse transactions.
          </p>
        </Panel>
      </div>
    );
  }

  return (
    <div data-testid="reversals-page">
      <PageHeader
        title="Reversal Console"
        subtitle="Fully unwind an eligible transaction. Reversal is irreversible and can only be done once per payment."
      />

      <div className="flex items-start gap-3 mb-6 rounded-lg border border-amber-500/20 bg-amber-500/5 p-4" data-testid="reversals-warning">
        <AlertTriangle className="h-5 w-5 text-amber-400 shrink-0 mt-0.5" />
        <p className="text-sm text-muted-foreground">
          A reversal posts a compensating ledger entry to unwind the original credit and marks the payment
          <span className="font-mono"> reversed</span>. This is a high-risk, one-time action — it cannot be undone.
        </p>
      </div>

      <Panel className="p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-border text-sm font-medium">Eligible for reversal</div>
        {loading ? (
          <EmptyState message="Loading transactions…" testid="reversals-loading" />
        ) : eligible.length === 0 ? (
          <EmptyState message="No transactions are currently eligible for reversal." testid="reversals-empty" />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Reference</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {eligible.map((p) => (
                  <TableRow key={p.id} data-testid={`reversal-eligible-row-${p.id}`}>
                    <TableCell className="font-mono text-xs">{p.reference}</TableCell>
                    <TableCell className="text-right font-mono">{money(p.amount_minor, p.currency)}</TableCell>
                    <TableCell><StatusBadge status={p.status} /></TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{new Date(p.created_at).toLocaleString()}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="outline" size="sm" data-testid={`reverse-button-${p.id}`}
                        onClick={() => openConfirm(p)}>
                        <Undo2 className="h-4 w-4 mr-2" /> Reverse
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Panel>

      {reversed.length > 0 && (
        <Panel className="p-0 overflow-hidden mt-6" data-testid="reversals-history">
          <div className="px-4 py-3 border-b border-border text-sm font-medium">Reversed transactions</div>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Reference</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {reversed.map((p) => (
                  <TableRow key={p.id} data-testid={`reversal-done-row-${p.id}`}>
                    <TableCell className="font-mono text-xs">{p.reference}</TableCell>
                    <TableCell className="text-right font-mono">{money(p.amount_minor, p.currency)}</TableCell>
                    <TableCell><StatusBadge status={p.status} /></TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{new Date(p.created_at).toLocaleString()}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </Panel>
      )}

      <Dialog open={!!target} onOpenChange={(o) => { if (!o) closeConfirm(); }}>
        <DialogContent data-testid="reverse-confirm-dialog">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-red-400" /> Confirm reversal
            </DialogTitle>
            <DialogDescription>
              This will reverse <span className="font-mono">{target?.reference}</span> for{" "}
              <span className="font-mono">{target ? money(target.amount_minor, target.currency) : ""}</span>.
              This action is irreversible and cannot be repeated for this payment.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <label className="text-sm text-muted-foreground" htmlFor="reversal-reason">Reason (optional)</label>
            <Textarea id="reversal-reason" data-testid="reverse-reason-input" value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. duplicate charge, customer dispute" rows={3} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={closeConfirm} disabled={submitting} data-testid="reverse-cancel">Cancel</Button>
            <Button variant="destructive" onClick={confirmReverse} disabled={submitting} data-testid="reverse-confirm">
              {submitting ? "Reversing…" : "Reverse transaction"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
