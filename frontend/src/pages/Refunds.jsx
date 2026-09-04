import { useEffect, useState, useCallback } from "react";
import { api, money } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, StatusBadge, EmptyState, MethodBadge } from "@/components/common";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const methodOf = (r) => (r.provider_key === "demo_upi" ? "upi" : "card");

export default function Refunds() {
  const { selectedTenantId } = useAuth();
  const [refunds, setRefunds] = useState([]);
  const [methodFilter, setMethodFilter] = useState("all");
  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const { data } = await api.get("/payments/refunds/all", { params: { tenant_id: selectedTenantId } });
    setRefunds(data);
  }, [selectedTenantId]);
  useEffect(() => { load(); }, [load]);

  const filtered = methodFilter === "all" ? refunds : refunds.filter((r) => methodOf(r) === methodFilter);

  return (
    <div data-testid="refunds-page">
      <PageHeader title="Refunds & Reversals" subtitle="All refund activity, reconciled against the ledger." />
      <div className="flex items-center gap-2 mb-4" data-testid="refund-method-filter">
        {[["all", "All"], ["upi", "UPI"], ["card", "Card"]].map(([key, label]) => (
          <button key={key} type="button" data-testid={`refund-filter-${key}`}
            onClick={() => setMethodFilter(key)}
            className={`text-xs font-mono px-3 py-1.5 rounded-md border transition-colors ${
              methodFilter === key
                ? "bg-primary/20 border-primary/60 text-primary"
                : "bg-secondary/40 border-border text-muted-foreground hover:border-primary/40"
            }`}>
            {label}
            {key !== "all" && (
              <span className="ml-1.5 opacity-70">{refunds.filter((r) => methodOf(r) === key).length}</span>
            )}
          </button>
        ))}
      </div>
      <Panel className="p-0 overflow-hidden">
        {filtered.length === 0 ? (
          <EmptyState message={methodFilter === "all" ? "No refunds recorded yet." : `No ${methodFilter.toUpperCase()} refunds.`} testid="refunds-empty" />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Refund ID</TableHead>
                  <TableHead>Method</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((r) => (
                  <TableRow key={r.id} data-testid={`refund-row-${r.id}`}>
                    <TableCell className="font-mono text-xs">{r.id.slice(0, 8)}</TableCell>
                    <TableCell><MethodBadge method={methodOf(r)} testid={`refund-method-${r.id.slice(0, 8)}`} /></TableCell>
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
