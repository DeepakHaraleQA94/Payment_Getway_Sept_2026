import { useEffect, useState, useCallback } from "react";
import { api, money, downloadCsv } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, EmptyState } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Download } from "lucide-react";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export default function Ledger() {
  const { selectedTenantId } = useAuth();
  const [accounts, setAccounts] = useState([]);
  const [entries, setEntries] = useState([]);

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const p = { params: { tenant_id: selectedTenantId } };
    const [a, e] = await Promise.all([api.get("/ledger/accounts", p), api.get("/ledger/entries", p)]);
    setAccounts(a.data);
    setEntries(e.data);
  }, [selectedTenantId]);
  useEffect(() => { load(); }, [load]);

  return (
    <div data-testid="ledger-page">
      <PageHeader title="Balance & Ledger" subtitle="Double-entry balances and the append-only ledger."
        action={
          <Button variant="outline" data-testid="export-ledger-csv"
            onClick={() => downloadCsv("/reports/export/ledger.csv", { tenant_id: selectedTenantId }, "ledger.csv")}>
            <Download className="h-4 w-4 mr-2" /> Export CSV
          </Button>
        }
      />
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 md:gap-6 mb-6">
        {accounts.length === 0 && (
          <Panel><EmptyState message="No ledger accounts yet." testid="ledger-accounts-empty" /></Panel>
        )}
        {accounts.map((a) => (
          <Panel key={a.id} className="cp-anim" >
            <p className="text-xs font-mono uppercase tracking-wider text-muted-foreground">{a.account_type} · {a.currency}</p>
            <p className="font-mono text-2xl font-semibold mt-2" data-testid={`balance-${a.currency}-${a.account_type}`}>
              {money(a.balance_minor, a.currency)}
            </p>
          </Panel>
        ))}
      </div>

      <Panel className="p-0 overflow-hidden">
        <div className="px-5 py-4 border-b border-border"><h3 className="font-heading text-lg font-medium">Ledger Entries</h3></div>
        {entries.length === 0 ? (
          <EmptyState message="No ledger entries yet." testid="ledger-entries-empty" />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Direction</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead className="text-right">Balance After</TableHead>
                  <TableHead>Ref</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>Time</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {entries.map((e) => (
                  <TableRow key={e.id} data-testid={`ledger-entry-${e.id}`}>
                    <TableCell>
                      <span className={`font-mono text-xs ${e.direction === "credit" ? "text-emerald-400" : "text-red-400"}`}>
                        {e.direction === "credit" ? "+ CREDIT" : "- DEBIT"}
                      </span>
                    </TableCell>
                    <TableCell className="text-right font-mono">{money(e.amount_minor, e.currency)}</TableCell>
                    <TableCell className="text-right font-mono text-muted-foreground">{money(e.balance_after_minor, e.currency)}</TableCell>
                    <TableCell className="font-mono text-xs">{e.ref_type || "—"}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{e.description || "—"}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{new Date(e.created_at).toLocaleString()}</TableCell>
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
