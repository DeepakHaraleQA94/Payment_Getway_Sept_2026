import { useEffect, useState, useCallback, useRef } from "react";
import { Banknote, Download, Upload, FileDown, CheckCircle2, Copy, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { api, money, formatApiError, downloadCsv } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, StatusBadge, EmptyState } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";

const IMPORT_TEMPLATE =
  "provider_settlement_ref,currency,gross_minor,fees_minor,net_minor,txn_count\n" +
  "PSP-2026-06-01,USD,1000000,29000,971000,120\n";

const PREVIEW_META = {
  new: { label: "New", cls: "text-emerald-400", Icon: CheckCircle2 },
  duplicate: { label: "Duplicate", cls: "text-amber-400", Icon: Copy },
  error: { label: "Error", cls: "text-red-400", Icon: AlertTriangle },
};

export default function Settlements() {
  const { selectedTenantId, tenants } = useAuth();
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [preview, setPreview] = useState(null); // { items, created_count, duplicate_count, error_count }
  const [pendingFile, setPendingFile] = useState(null);
  const [imports, setImports] = useState([]);
  const [detail, setDetail] = useState(null);
  const fileInputRef = useRef(null);
  const tenant = tenants.find((t) => t.id === selectedTenantId);

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const { data } = await api.get("/settlements", { params: { tenant_id: selectedTenantId } });
    setRows(data);
  }, [selectedTenantId]);
  const loadImports = useCallback(async () => {
    if (!selectedTenantId) return;
    try {
      const { data } = await api.get("/settlements/imports", { params: { tenant_id: selectedTenantId } });
      setImports(data);
    } catch { /* history is best-effort */ }
  }, [selectedTenantId]);
  useEffect(() => { load(); loadImports(); }, [load, loadImports]);

  const generate = async () => {
    setBusy(true);
    try {
      await api.post("/settlements/generate", {}, {
        params: { tenant_id: selectedTenantId, currency: tenant?.default_currency || "USD" },
      });
      toast.success("Settlement batch generated");
      load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const downloadTemplate = () => {
    const url = window.URL.createObjectURL(new Blob([IMPORT_TEMPLATE], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "settlement_import_template.csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  };

  // Step 1: pick a file -> dry-run preview (nothing is written).
  const onFilePicked = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setImporting(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const { data } = await api.post("/settlements/import", form, {
        params: { tenant_id: selectedTenantId, dry_run: true },
        headers: { "Content-Type": "multipart/form-data" },
      });
      setPendingFile(file);
      setPreview(data);
    } catch (err) { toast.error(formatApiError(err.response?.data?.detail)); }
    finally { setImporting(false); }
  };

  // Step 2: confirm -> real import (dry_run=false) commits only the new rows.
  const confirmImport = async () => {
    if (!pendingFile) return;
    setImporting(true);
    try {
      const form = new FormData();
      form.append("file", pendingFile);
      const { data } = await api.post("/settlements/import", form, {
        params: { tenant_id: selectedTenantId },
        headers: { "Content-Type": "multipart/form-data" },
      });
      const parts = [`${data.created_count} created`];
      if (data.duplicate_count) parts.push(`${data.duplicate_count} duplicate (skipped)`);
      if (data.error_count) parts.push(`${data.error_count} error(s)`);
      toast.success(`Import complete — ${parts.join(", ")}`);
      setPreview(null); setPendingFile(null);
      load(); loadImports();
    } catch (err) { toast.error(formatApiError(err.response?.data?.detail)); }
    finally { setImporting(false); }
  };

  const closePreview = () => { setPreview(null); setPendingFile(null); };

  const openDetail = async (settlement) => {
    setDetail({ loading: true });
    try {
      const { data } = await api.get(`/settlements/${settlement.id}`, { params: { tenant_id: selectedTenantId } });
      setDetail(data);
    } catch (e) {
      toast.error(formatApiError(e.response?.data?.detail));
      setDetail(null);
    }
  };

  return (
    <div data-testid="settlements-page">
      <input ref={fileInputRef} type="file" accept=".csv,text/csv" className="hidden"
        data-testid="settlement-import-input" onChange={onFilePicked} />
      <PageHeader
        title="Settlement & Reconciliation"
        subtitle="Batch captured payments, or import a provider settlement file and reconcile it idempotently."
        action={
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" data-testid="settlement-template-button" onClick={downloadTemplate}>
              <FileDown className="h-4 w-4 mr-2" /> CSV Template
            </Button>
            <Button variant="outline" data-testid="settlement-import-button"
              onClick={() => fileInputRef.current?.click()} disabled={importing || !selectedTenantId}>
              <Upload className="h-4 w-4 mr-2" /> {importing && !preview ? "Reading…" : "Import File"}
            </Button>
            <Button variant="outline" data-testid="export-settlements-csv"
              onClick={() => downloadCsv("/reports/export/settlements.csv", { tenant_id: selectedTenantId }, "settlements.csv")}>
              <Download className="h-4 w-4 mr-2" /> Export CSV
            </Button>
            <Button data-testid="generate-settlement-button" onClick={generate} disabled={busy}><Banknote className="h-4 w-4 mr-2" /> Generate Settlement</Button>
          </div>
        }
      />

      <Dialog open={!!preview} onOpenChange={(o) => { if (!o) closePreview(); }}>
        <DialogContent className="max-w-3xl" data-testid="settlement-preview-dialog">
          <DialogHeader>
            <DialogTitle>Import preview — dry run</DialogTitle>
            <DialogDescription>
              Nothing has been saved yet. Review the rows below, then confirm to import the new ones.
              Duplicates and errors are skipped.
            </DialogDescription>
          </DialogHeader>
          {preview && (
            <>
              <div className="flex gap-4 text-sm" data-testid="settlement-preview-counts">
                <span className="text-emerald-400">{preview.created_count} new</span>
                <span className="text-amber-400">{preview.duplicate_count} duplicate</span>
                <span className="text-red-400">{preview.error_count} error(s)</span>
              </div>
              <div className="max-h-[45vh] overflow-auto border border-border rounded-md">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Reference</TableHead>
                      <TableHead className="text-right">Net</TableHead>
                      <TableHead>Txns</TableHead>
                      <TableHead>Outcome</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {preview.items.map((it, idx) => {
                      const meta = PREVIEW_META[it.status] || PREVIEW_META.error;
                      return (
                        <TableRow key={idx} data-testid={`settlement-preview-row-${it.status}`}>
                          <TableCell className="font-mono text-xs">{it.provider_settlement_ref || `(row ${it.row})`}</TableCell>
                          <TableCell className="text-right font-mono">
                            {it.net_minor != null ? money(it.net_minor, it.currency || "USD") : "—"}
                          </TableCell>
                          <TableCell className="font-mono text-xs">{it.txn_count ?? "—"}</TableCell>
                          <TableCell>
                            <span className={`inline-flex items-center gap-1 text-xs ${meta.cls}`}>
                              <meta.Icon className="h-3.5 w-3.5" /> {meta.label}
                              {it.error ? <span className="text-muted-foreground ml-1">· {it.error}</span> : null}
                            </span>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={closePreview} data-testid="settlement-preview-cancel">Cancel</Button>
            <Button onClick={confirmImport} disabled={importing || !preview || preview.created_count === 0}
              data-testid="settlement-preview-confirm">
              {importing ? "Importing…" : `Confirm import (${preview?.created_count || 0})`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Panel className="p-0 overflow-hidden">
        {rows.length === 0 ? (
          <EmptyState message="No settlements generated yet." testid="settlements-empty" />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Reference</TableHead>
                  <TableHead className="text-right">Gross</TableHead>
                  <TableHead className="text-right">Fees</TableHead>
                  <TableHead className="text-right">Net</TableHead>
                  <TableHead>Txns</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((s) => (
                  <TableRow key={s.id} data-testid={`settlement-row-${s.reference}`}>
                    <TableCell className="font-mono text-xs">{s.reference}</TableCell>
                    <TableCell className="text-right font-mono">{money(s.gross_minor, s.currency)}</TableCell>
                    <TableCell className="text-right font-mono text-muted-foreground">{money(s.fees_minor, s.currency)}</TableCell>
                    <TableCell className="text-right font-mono">{money(s.net_minor, s.currency)}</TableCell>
                    <TableCell className="font-mono text-xs">{s.txn_count}</TableCell>
                    <TableCell><StatusBadge status={s.status} /></TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{new Date(s.created_at).toLocaleString()}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="outline" size="sm" data-testid={`settlement-view-${s.reference}`} onClick={() => openDetail(s)}>View</Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Panel>

      {imports.length > 0 && (
        <Panel className="p-0 overflow-hidden mt-6" data-testid="settlement-import-history">
          <div className="px-4 py-3 border-b border-border text-sm font-medium">Import history</div>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>When</TableHead>
                  <TableHead>By</TableHead>
                  <TableHead>File</TableHead>
                  <TableHead className="text-right">New</TableHead>
                  <TableHead className="text-right">Duplicate</TableHead>
                  <TableHead className="text-right">Errors</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {imports.map((h) => (
                  <TableRow key={h.id} data-testid="settlement-import-history-row">
                    <TableCell className="font-mono text-xs text-muted-foreground">{new Date(h.created_at).toLocaleString()}</TableCell>
                    <TableCell className="text-xs">{h.actor_email || "—"}</TableCell>
                    <TableCell className="font-mono text-xs">{h.filename || "—"}</TableCell>
                    <TableCell className="text-right font-mono text-emerald-400">{h.created}</TableCell>
                    <TableCell className="text-right font-mono text-amber-400">{h.duplicates}</TableCell>
                    <TableCell className="text-right font-mono text-red-400">{h.errors}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </Panel>
      )}

      <Dialog open={!!detail} onOpenChange={(o) => { if (!o) setDetail(null); }}>
        <DialogContent className="max-w-2xl" data-testid="settlement-detail-dialog">
          <DialogHeader>
            <DialogTitle>Settlement detail</DialogTitle>
            <DialogDescription>Read-only view of this settlement and related reconciliation context.</DialogDescription>
          </DialogHeader>
          {detail?.loading ? (
            <EmptyState message="Loading settlement…" testid="settlement-detail-loading" />
          ) : detail ? (
            <div className="space-y-4 text-sm">
              <div className="grid grid-cols-2 gap-x-6 gap-y-2">
                {[
                  ["Reference", detail.reference],
                  ["Provider settlement ref", detail.provider_settlement_ref || "—"],
                  ["Tenant", detail.tenant_name || detail.tenant_id],
                  ["Currency", detail.currency],
                  ["Gross", money(detail.gross_minor, detail.currency)],
                  ["Fees", money(detail.fees_minor, detail.currency)],
                  ["Net", money(detail.net_minor, detail.currency)],
                  ["Transactions", detail.txn_count],
                  ["Status", detail.status],
                  ["Import source", detail.import_source],
                  ["Created", new Date(detail.created_at).toLocaleString()],
                  ["Settled", detail.settled_at ? new Date(detail.settled_at).toLocaleString() : "—"],
                  ["Created by", detail.created_by_email || "—"],
                  ["Settlement ID", detail.id],
                ].map(([k, v]) => (
                  <div key={k} className="flex flex-col">
                    <span className="text-xs text-muted-foreground">{k}</span>
                    <span className="font-mono text-xs break-all" data-testid={`settlement-detail-${k.toLowerCase().replace(/[^a-z]+/g, '-')}`}>{v}</span>
                  </div>
                ))}
              </div>
              <div>
                <div className="text-xs font-medium mb-1">Reconciliation context</div>
                <p className="text-xs text-muted-foreground mb-2">{detail.reconciliation_context?.note}</p>
                {detail.reconciliation_context?.runs?.length ? (
                  <div className="border border-border rounded-md overflow-hidden max-h-56 overflow-y-auto">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>When</TableHead>
                          <TableHead>Source</TableHead>
                          <TableHead className="text-right">Lines</TableHead>
                          <TableHead className="text-right">Matched</TableHead>
                          <TableHead className="text-right">Discrepancies</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {detail.reconciliation_context.runs.map((r) => (
                          <TableRow key={r.id} data-testid="settlement-detail-recon-row">
                            <TableCell className="font-mono text-xs text-muted-foreground">{new Date(r.created_at).toLocaleString()}</TableCell>
                            <TableCell className="text-xs">{r.source}</TableCell>
                            <TableCell className="text-right font-mono">{r.total_lines}</TableCell>
                            <TableCell className="text-right font-mono text-emerald-400">{r.matched_count}</TableCell>
                            <TableCell className="text-right font-mono text-red-400">{r.discrepancy_count}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground" data-testid="settlement-detail-recon-empty">No reconciliation runs for this tenant/currency.</p>
                )}
              </div>
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDetail(null)} data-testid="settlement-detail-close">Back to list</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
