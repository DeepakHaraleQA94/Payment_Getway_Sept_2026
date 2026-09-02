import { useEffect, useState, useCallback } from "react";
import { Landmark, Check, X, Plus, ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import { api, money, formatApiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, StatusBadge, EmptyState } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";

export default function UtrConsole() {
  const { selectedTenantId, hasPermission } = useAuth();
  const canVerify = hasPermission("utr.verify");
  const canSubmit = hasPermission("utr.submit");
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [review, setReview] = useState(null); // { submission, decision }
  const [expectedAmount, setExpectedAmount] = useState("");
  const [expectedCurrency, setExpectedCurrency] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [showSubmit, setShowSubmit] = useState(false);
  const [form, setForm] = useState({ utr: "", amount_minor: "", currency: "USD", payment_id: "" });

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    setLoading(true);
    try {
      const { data } = await api.get("/payments/utr/list", { params: { tenant_id: selectedTenantId } });
      setRows(data);
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail));
    } finally {
      setLoading(false);
    }
  }, [selectedTenantId]);
  useEffect(() => { load(); }, [load]);

  const pending = rows.filter((r) => r.status === "under_review");
  const resolved = rows.filter((r) => r.status !== "under_review");

  const openReview = (submission, decision) => {
    setReview({ submission, decision });
    setExpectedAmount(String(submission.amount_minor));
    setExpectedCurrency(submission.currency);
    setReason("");
  };
  const closeReview = () => { if (!submitting) { setReview(null); setReason(""); } };

  const confirmReview = async () => {
    if (!review) return;
    setSubmitting(true);
    try {
      const body = { decision: review.decision };
      if (review.decision === "confirm") {
        if (expectedAmount !== "") body.expected_amount_minor = parseInt(expectedAmount, 10);
        if (expectedCurrency) body.expected_currency = expectedCurrency;
      }
      if (reason) body.reason = reason;
      const { data } = await api.post(`/payments/utr/${review.submission.id}/review`, body);
      toast.success(`UTR ${data.status}`);
      setReview(null); setReason("");
      load();
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail));
    } finally {
      setSubmitting(false);
    }
  };

  const submitUtr = async () => {
    setSubmitting(true);
    try {
      const payload = {
        utr: form.utr.trim(),
        amount_minor: parseInt(form.amount_minor, 10),
        currency: form.currency || "USD",
      };
      if (form.payment_id.trim()) payload.payment_id = form.payment_id.trim();
      await api.post("/payments/utr", payload, { params: { tenant_id: selectedTenantId } });
      toast.success("UTR submitted for review");
      setShowSubmit(false);
      setForm({ utr: "", amount_minor: "", currency: "USD", payment_id: "" });
      load();
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail));
    } finally {
      setSubmitting(false);
    }
  };

  const submitBtn = canSubmit && (
    <Button variant="outline" data-testid="utr-open-submit" onClick={() => setShowSubmit(true)}>
      <Plus className="h-4 w-4 mr-2" /> Submit UTR
    </Button>
  );

  if (!canVerify && !canSubmit) {
    return (
      <div data-testid="utr-page">
        <PageHeader title="UTR Console" subtitle="Review bank transfer references (UTRs)." />
        <Panel className="flex items-center gap-3" data-testid="utr-no-permission">
          <ShieldAlert className="h-5 w-5 text-amber-400" />
          <p className="text-sm text-muted-foreground">
            You don't have permission to view or review UTR submissions.
          </p>
        </Panel>
      </div>
    );
  }

  return (
    <div data-testid="utr-page">
      <PageHeader
        title="UTR Console"
        subtitle="Manually verify bank transfer references. A UTR is credited only after an authorized confirmation."
        action={submitBtn}
      />

      <Panel className="p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-border text-sm font-medium">Pending review</div>
        {loading ? (
          <EmptyState message="Loading UTR submissions…" testid="utr-loading" />
        ) : pending.length === 0 ? (
          <EmptyState message="No UTR submissions awaiting review." testid="utr-empty" />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>UTR</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead>Linked payment</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Submitted</TableHead>
                  {canVerify && <TableHead className="text-right">Actions</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {pending.map((r) => (
                  <TableRow key={r.id} data-testid={`utr-pending-row-${r.id}`}>
                    <TableCell className="font-mono text-xs">{r.utr}</TableCell>
                    <TableCell className="text-right font-mono">{money(r.amount_minor, r.currency)}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{r.payment_id ? r.payment_id.slice(0, 8) : "—"}</TableCell>
                    <TableCell><StatusBadge status={r.status} /></TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{new Date(r.created_at).toLocaleString()}</TableCell>
                    {canVerify && (
                      <TableCell className="text-right whitespace-nowrap">
                        <Button variant="outline" size="sm" className="mr-2 text-emerald-400 border-emerald-500/30"
                          data-testid={`utr-approve-${r.id}`} onClick={() => openReview(r, "confirm")}>
                          <Check className="h-4 w-4 mr-1" /> Approve
                        </Button>
                        <Button variant="outline" size="sm" className="text-red-400 border-red-500/30"
                          data-testid={`utr-reject-${r.id}`} onClick={() => openReview(r, "reject")}>
                          <X className="h-4 w-4 mr-1" /> Reject
                        </Button>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Panel>

      {resolved.length > 0 && (
        <Panel className="p-0 overflow-hidden mt-6" data-testid="utr-resolved">
          <div className="px-4 py-3 border-b border-border text-sm font-medium">Resolved</div>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>UTR</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead>Submitted</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {resolved.map((r) => (
                  <TableRow key={r.id} data-testid={`utr-resolved-row-${r.id}`}>
                    <TableCell className="font-mono text-xs">{r.utr}</TableCell>
                    <TableCell className="text-right font-mono">{money(r.amount_minor, r.currency)}</TableCell>
                    <TableCell><StatusBadge status={r.status} /></TableCell>
                    <TableCell className="text-sm text-muted-foreground">{r.reason || "—"}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{new Date(r.created_at).toLocaleString()}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </Panel>
      )}

      {/* Review confirmation */}
      <Dialog open={!!review} onOpenChange={(o) => { if (!o) closeReview(); }}>
        <DialogContent data-testid="utr-review-dialog">
          <DialogHeader>
            <DialogTitle>{review?.decision === "confirm" ? "Approve UTR" : "Reject UTR"}</DialogTitle>
            <DialogDescription>
              {review?.decision === "confirm"
                ? "Confirm only after you have verified this bank transfer. Confirmation credits the ledger and cannot be undone."
                : "Reject this UTR submission. No ledger entry will be created."}
              {" "}UTR <span className="font-mono">{review?.submission?.utr}</span> for{" "}
              <span className="font-mono">{review ? money(review.submission.amount_minor, review.submission.currency) : ""}</span>.
            </DialogDescription>
          </DialogHeader>
          {review?.decision === "confirm" && (
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <label className="text-sm text-muted-foreground" htmlFor="utr-expected-amount">Verified amount (minor)</label>
                <Input id="utr-expected-amount" data-testid="utr-expected-amount" type="number" value={expectedAmount}
                  onChange={(e) => setExpectedAmount(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <label className="text-sm text-muted-foreground" htmlFor="utr-expected-currency">Verified currency</label>
                <Input id="utr-expected-currency" data-testid="utr-expected-currency" value={expectedCurrency}
                  onChange={(e) => setExpectedCurrency(e.target.value.toUpperCase())} maxLength={3} />
              </div>
            </div>
          )}
          <div className="space-y-1.5">
            <label className="text-sm text-muted-foreground" htmlFor="utr-reason">Reason (optional)</label>
            <Textarea id="utr-reason" data-testid="utr-reason" value={reason}
              onChange={(e) => setReason(e.target.value)} rows={2} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={closeReview} disabled={submitting} data-testid="utr-review-cancel">Cancel</Button>
            <Button variant={review?.decision === "confirm" ? "default" : "destructive"}
              onClick={confirmReview} disabled={submitting} data-testid="utr-review-confirm">
              {submitting ? "Working…" : review?.decision === "confirm" ? "Approve & credit" : "Reject"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Submit UTR */}
      <Dialog open={showSubmit} onOpenChange={(o) => { if (!o && !submitting) setShowSubmit(false); }}>
        <DialogContent data-testid="utr-submit-dialog">
          <DialogHeader>
            <DialogTitle>Submit a UTR</DialogTitle>
            <DialogDescription>Record a bank transfer reference for review. It is never credited on submission.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <label className="text-sm text-muted-foreground" htmlFor="submit-utr">Bank UTR reference</label>
              <Input id="submit-utr" data-testid="utr-submit-ref" value={form.utr}
                onChange={(e) => setForm({ ...form, utr: e.target.value })} placeholder="e.g. UTR2026060112345" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <label className="text-sm text-muted-foreground" htmlFor="submit-amount">Amount (minor)</label>
                <Input id="submit-amount" data-testid="utr-submit-amount" type="number" value={form.amount_minor}
                  onChange={(e) => setForm({ ...form, amount_minor: e.target.value })} placeholder="e.g. 500000" />
              </div>
              <div className="space-y-1.5">
                <label className="text-sm text-muted-foreground" htmlFor="submit-currency">Currency</label>
                <Input id="submit-currency" data-testid="utr-submit-currency" value={form.currency}
                  onChange={(e) => setForm({ ...form, currency: e.target.value.toUpperCase() })} maxLength={3} />
              </div>
            </div>
            <div className="space-y-1.5">
              <label className="text-sm text-muted-foreground" htmlFor="submit-payment">Linked payment ID (optional)</label>
              <Input id="submit-payment" data-testid="utr-submit-payment" value={form.payment_id}
                onChange={(e) => setForm({ ...form, payment_id: e.target.value })} placeholder="payment UUID" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowSubmit(false)} disabled={submitting} data-testid="utr-submit-cancel">Cancel</Button>
            <Button onClick={submitUtr} data-testid="utr-submit-confirm"
              disabled={submitting || !form.utr.trim() || !form.amount_minor}>
              {submitting ? "Submitting…" : "Submit for review"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
