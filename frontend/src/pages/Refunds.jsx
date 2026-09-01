import { useEffect, useState, useCallback } from "react";
import { api, money } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, StatusBadge, EmptyState } from "@/components/common";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export default function Refunds() {
  const { selectedTenantId } = useAuth();
  const [refunds, setRefunds] = useState([]);
  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const { data } = await api.get("/payments/refunds/all", { params: { tenant_id: selectedTenantId } });
    setRefunds(data);
  }, [selectedTenantId]);
  useEffect(() => { load(); }, [load]);

  return (
    <div data-testid="refunds-page">
      <PageHeader title="Refunds & Reversals" subtitle="All refund activity, reconciled against the ledger." />
      <Panel className="p-0 overflow-hidden">
        {refunds.length === 0 ? (
          <EmptyState message="No refunds recorded yet." testid="refunds-empty" />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Refund ID</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {refunds.map((r) => (
                  <TableRow key={r.id} data-testid={`refund-row-${r.id}`}>
                    <TableCell className="font-mono text-xs">{r.id.slice(0, 8)}</TableCell>
                    <TableCell className="text-right font-mono">{money(r.amount_minor, r.currency)}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{r.reason || "—"}</TableCell>
                    <TableCell><StatusBadge status={r.status} /></TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{new Date(r.created_at).toLocaleString()}</TableCell>
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
