import { useEffect, useState, useCallback, useRef } from "react";
import { Banknote, Download, Upload, FileDown } from "lucide-react";
import { toast } from "sonner";
import { api, money, formatApiError, downloadCsv } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, StatusBadge, EmptyState } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const IMPORT_TEMPLATE =
  "provider_settlement_ref,currency,gross_minor,fees_minor,net_minor,txn_count\n" +
  "PSP-2026-06-01,USD,1000000,29000,971000,120\n";

export default function Settlements() {
  const { selectedTenantId, tenants } = useAuth();
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const fileInputRef = useRef(null);
  const tenant = tenants.find((t) => t.id === selectedTenantId);

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const { data } = await api.get("/settlements", { params: { tenant_id: selectedTenantId } });
    setRows(data);
  }, [selectedTenantId]);
  useEffect(() => { load(); }, [load]);

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

  const onFilePicked = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file
    if (!file) return;
    setImporting(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const { data } = await api.post("/settlements/import", form, {
        params: { tenant_id: selectedTenantId },
        headers: { "Content-Type": "multipart/form-data" },
      });
      const parts = [`${data.created_count} created`];
      if (data.duplicate_count) parts.push(`${data.duplicate_count} duplicate (skipped)`);
      if (data.error_count) parts.push(`${data.error_count} error(s)`);
      toast.success(`Import complete — ${parts.join(", ")}`);
      load();
    } catch (err) { toast.error(formatApiError(err.response?.data?.detail)); }
    finally { setImporting(false); }
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
              <Upload className="h-4 w-4 mr-2" /> {importing ? "Importing…" : "Import File"}
            </Button>
            <Button variant="outline" data-testid="export-settlements-csv"
              onClick={() => downloadCsv("/reports/export/settlements.csv", { tenant_id: selectedTenantId }, "settlements.csv")}>
              <Download className="h-4 w-4 mr-2" /> Export CSV
            </Button>
            <Button data-testid="generate-settlement-button" onClick={generate} disabled={busy}><Banknote className="h-4 w-4 mr-2" /> Generate Settlement</Button>
          </div>
        }
      />
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
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Panel>
    </div>
  );
}
