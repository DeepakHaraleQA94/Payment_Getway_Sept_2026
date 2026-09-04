import { useEffect, useState, useCallback } from "react";
import { GitCompareArrows, Upload, FileDown, ShieldAlert, RefreshCw, Download } from "lucide-react";
import { toast } from "sonner";
import { api, money, formatApiError, downloadCsv } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, EmptyState } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";

const TEMPLATE =
  "provider_txn_id,reference,amount_minor,currency,status\n" +
  "mock_abc123,ORD-1001,10000,USD,succeeded\n";

const OUTCOMES = ["matched", "amount_mismatch", "currency_mismatch", "status_mismatch",
  "missing_in_cloudpay", "missing_at_provider", "duplicate"];

const OUTCOME_CLS = {
  matched: "text-emerald-400",
  amount_mismatch: "text-red-400",
  currency_mismatch: "text-red-400",
  status_mismatch: "text-amber-400",
  missing_in_cloudpay: "text-amber-400",
  missing_at_provider: "text-amber-400",
  duplicate: "text-red-400",
};

export default function Reconciliation() {
  const { selectedTenantId, hasPermission } = useAuth();
  const canView = hasPermission("reconciliation.view");
  const canRun = hasPermission("reconciliation.run");
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showRun, setShowRun] = useState(false);
  const [running, setRunning] = useState(false);
  const [form, setForm] = useState({ source: "both", currency: "", run_ref: "", file: null });
  const [detail, setDetail] = useState(null);
  const [filter, setFilter] = useState("");

  const load = useCallback(async () => {
    if (!selectedTenantId || !canView) return;
    setLoading(true);
    try {
      const { data } = await api.get("/reconciliation/runs", { params: { tenant_id: selectedTenantId } });
      setRuns(data);
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
    finally { setLoading(false); }
  }, [selectedTenantId, canView]);
  useEffect(() => { load(); }, [load]);

  const downloadTemplate = () => {
    const url = window.URL.createObjectURL(new Blob([TEMPLATE], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url; a.download = "reconciliation_template.csv";
    document.body.appendChild(a); a.click(); a.remove();
    window.URL.revokeObjectURL(url);
  };

  const runReconciliation = async () => {
    setRunning(true);
    try {
      const fd = new FormData();
      if (form.file) fd.append("file", form.file);
      const params = { tenant_id: selectedTenantId, source: form.source };
      if (form.currency) params.currency = form.currency.toUpperCase();
      if (form.run_ref) params.run_ref = form.run_ref;
      const { data } = await api.post("/reconciliation/run", fd, {
        params, headers: { "Content-Type": "multipart/form-data" },
      });
      toast.success(`Reconciled ${data.total_lines} — ${data.matched_count} matched, ${data.discrepancy_count} discrepancies`);
      setShowRun(false);
      setForm({ source: "both", currency: "", run_ref: "", file: null });
      load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
    finally { setRunning(false); }
  };

  const openDetail = async (run) => {
    setFilter("");
    try {
      const { data } = await api.get(`/reconciliation/runs/${run.id}`, { params: { tenant_id: selectedTenantId } });
      setDetail(data);
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };

  if (!canView) {
    return (
      <div data-testid="reconciliation-page">
        <PageHeader title="Reconciliation" subtitle="Line-level payment reconciliation & matching." />
        <Panel className="flex items-center gap-3" data-testid="reconciliation-no-permission">
          <ShieldAlert className="h-5 w-5 text-amber-400" />
          <p className="text-sm text-muted-foreground">You don't have permission to view reconciliation.</p>
        </Panel>
      </div>
    );
  }

  const filteredItems = detail ? detail.items.filter((i) => !filter || i.outcome === filter) : [];

  return (
    <div data-testid="reconciliation-page">
      <PageHeader
        title="Reconciliation"
        subtitle="Match internal payments against provider records. Report-only — never changes balances or the ledger."
        action={
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" data-testid="reconciliation-template" onClick={downloadTemplate}>
              <FileDown className="h-4 w-4 mr-2" /> CSV Template
            </Button>
            <Button variant="outline" data-testid="reconciliation-refresh" onClick={load}>
              <RefreshCw className="h-4 w-4 mr-2" /> Refresh
            </Button>
            {canRun && (
              <Button data-testid="reconciliation-open-run" onClick={() => setShowRun(true)} disabled={!selectedTenantId}>
                <GitCompareArrows className="h-4 w-4 mr-2" /> Run Reconciliation
              </Button>
            )}
          </div>
        }
      />

      <Panel className="p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-border text-sm font-medium">Reconciliation runs</div>
        {loading ? (
          <EmptyState message="Loading runs…" testid="reconciliation-loading" />
        ) : runs.length === 0 ? (
          <EmptyState message="No reconciliation runs yet. Run one to compare provider records against CloudPay." testid="reconciliation-empty" />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>When</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>File</TableHead>
                  <TableHead className="text-right">Lines</TableHead>
                  <TableHead className="text-right">Matched</TableHead>
                  <TableHead className="text-right">Discrepancies</TableHead>
                  <TableHead className="text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.map((r) => (
                  <TableRow key={r.id} data-testid={`reconciliation-run-row-${r.id}`}>
                    <TableCell className="font-mono text-xs text-muted-foreground">{new Date(r.created_at).toLocaleString()}</TableCell>
                    <TableCell className="text-xs">{r.source}</TableCell>
                    <TableCell className="font-mono text-xs">{r.filename || "—"}</TableCell>
                    <TableCell className="text-right font-mono">{r.total_lines}</TableCell>
                    <TableCell className="text-right font-mono text-emerald-400">{r.matched_count}</TableCell>
                    <TableCell className="text-right font-mono text-red-400">{r.discrepancy_count}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="outline" size="sm" data-testid={`reconciliation-view-${r.id}`} onClick={() => openDetail(r)}>View</Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Panel>

      {/* Run dialog */}
      <Dialog open={showRun} onOpenChange={(o) => { if (!o && !running) setShowRun(false); }}>
        <DialogContent data-testid="reconciliation-run-dialog">
          <DialogHeader>
            <DialogTitle>Run reconciliation</DialogTitle>
            <DialogDescription>Compare provider records against CloudPay payments. This is read-only and never changes balances.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <label className="text-sm text-muted-foreground" htmlFor="rc-source">Source</label>
              <select id="rc-source" data-testid="reconciliation-source"
                className="w-full h-10 rounded-md border border-input bg-background px-3 text-sm"
                value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })}>
                <option value="both">Both (upload + provider pull)</option>
                <option value="upload">Upload provider lines (CSV)</option>
                <option value="provider_pull">Provider status pull only</option>
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="text-sm text-muted-foreground" htmlFor="rc-file">Provider lines CSV {form.source === "provider_pull" ? "(not needed)" : ""}</label>
              <Input id="rc-file" type="file" accept=".csv,text/csv" data-testid="reconciliation-file"
                onChange={(e) => setForm({ ...form, file: e.target.files?.[0] || null })} disabled={form.source === "provider_pull"} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <label className="text-sm text-muted-foreground" htmlFor="rc-currency">Currency (optional)</label>
                <Input id="rc-currency" data-testid="reconciliation-currency" maxLength={3} value={form.currency}
                  onChange={(e) => setForm({ ...form, currency: e.target.value.toUpperCase() })} placeholder="e.g. USD" />
              </div>
              <div className="space-y-1.5">
                <label className="text-sm text-muted-foreground" htmlFor="rc-ref">Run ref (optional, idempotent)</label>
                <Input id="rc-ref" data-testid="reconciliation-run-ref" value={form.run_ref}
                  onChange={(e) => setForm({ ...form, run_ref: e.target.value })} placeholder="e.g. 2026-06-01" />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowRun(false)} disabled={running} data-testid="reconciliation-run-cancel">Cancel</Button>
            <Button onClick={runReconciliation} data-testid="reconciliation-run-confirm"
              disabled={running || (form.source === "upload" && !form.file)}>
              {running ? "Running…" : "Run"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Detail dialog */}
      <Dialog open={!!detail} onOpenChange={(o) => { if (!o) setDetail(null); }}>
        <DialogContent className="max-w-4xl" data-testid="reconciliation-detail-dialog">
          <DialogHeader>
            <DialogTitle>Reconciliation results</DialogTitle>
            <DialogDescription>
              {detail && `${detail.run.total_lines} lines · ${detail.run.matched_count} matched · ${detail.run.discrepancy_count} discrepancies`}
            </DialogDescription>
          </DialogHeader>
          {detail && (
            <>
              <div className="flex items-center gap-3 text-xs font-mono px-1 pb-1" data-testid="reconciliation-method-split">
                <span className="text-muted-foreground">By rail:</span>
                <span className="inline-flex items-center gap-1 text-primary">
                  UPI <span className="opacity-80">{detail.method_summary?.upi || 0}</span>
                </span>
                <span className="text-muted-foreground">·</span>
                <span className="inline-flex items-center gap-1">
                  Card <span className="opacity-80">{detail.method_summary?.card || 0}</span>
                </span>
                {detail.method_summary?.unknown ? (
                  <>
                    <span className="text-muted-foreground">·</span>
                    <span className="text-muted-foreground">Unknown {detail.method_summary.unknown}</span>
                  </>
                ) : null}
              </div>
              <div className="flex flex-wrap gap-2" data-testid="reconciliation-summary">
                <button className={`text-xs px-2 py-1 rounded border ${!filter ? "border-primary" : "border-border"}`}
                  data-testid="reconciliation-filter-all" onClick={() => setFilter("")}>All ({detail.items.length})</button>
                {OUTCOMES.map((o) => (
                  <button key={o} onClick={() => setFilter(o)} data-testid={`reconciliation-filter-${o}`}
                    className={`text-xs px-2 py-1 rounded border ${filter === o ? "border-primary" : "border-border"} ${OUTCOME_CLS[o]}`}>
                    {o} ({detail.run.summary?.[o] ?? 0})
                  </button>
                ))}
              </div>
              <div className="max-h-[50vh] overflow-auto border border-border rounded-md">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Outcome</TableHead>
                      <TableHead>Reference / Txn</TableHead>
                      <TableHead className="text-right">Provider</TableHead>
                      <TableHead className="text-right">Internal</TableHead>
                      <TableHead>Detail</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filteredItems.map((i) => (
                      <TableRow key={i.id} data-testid={`reconciliation-item-${i.outcome}`}>
                        <TableCell className={`text-xs font-medium ${OUTCOME_CLS[i.outcome]}`}>{i.outcome}</TableCell>
                        <TableCell className="font-mono text-xs">{i.reference || i.provider_txn_id || "—"}</TableCell>
                        <TableCell className="text-right font-mono text-xs">{i.provider_amount_minor != null ? money(i.provider_amount_minor, i.currency || "USD") : "—"}</TableCell>
                        <TableCell className="text-right font-mono text-xs">{i.internal_amount_minor != null ? money(i.internal_amount_minor, i.currency || "USD") : "—"}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">{i.detail}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
          <DialogFooter>
            {detail && (
              <Button variant="outline" data-testid="reconciliation-download-csv"
                onClick={() => downloadCsv(
                  `/reconciliation/runs/${detail.run.id}/export.csv`,
                  { tenant_id: selectedTenantId, ...(filter ? { outcome: filter } : {}) },
                  `reconciliation_${detail.run.run_ref || detail.run.id}.csv`,
                ).catch((e) => toast.error(formatApiError(e.response?.data?.detail)))}>
                <Download className="h-4 w-4 mr-2" /> Download CSV
              </Button>
            )}
            <Button variant="outline" onClick={() => setDetail(null)} data-testid="reconciliation-detail-close">Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
