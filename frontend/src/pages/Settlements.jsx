import { useEffect, useState, useCallback } from "react";
import { Banknote, Download } from "lucide-react";
import { toast } from "sonner";
import { api, money, formatApiError, downloadCsv } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, StatusBadge, EmptyState } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export default function Settlements() {
  const { selectedTenantId, tenants } = useAuth();
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
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

  return (
    <div data-testid="settlements-page">
      <PageHeader
        title="Settlement & Reconciliation"
        subtitle="Batch captured payments and reconcile net payouts."
        action={
          <div className="flex gap-2">
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
