import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, EmptyState } from "@/components/common";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export default function Audit() {
  const { selectedTenantId } = useAuth();
  const [logs, setLogs] = useState([]);
  const load = useCallback(async () => {
    const { data } = await api.get("/audit", { params: selectedTenantId ? { tenant_id: selectedTenantId } : {} });
    setLogs(data);
  }, [selectedTenantId]);
  useEffect(() => { load(); }, [load]);

  return (
    <div data-testid="audit-page">
      <PageHeader title="Audit Log" subtitle="Append-only trail of every financial and administrative mutation." />
      <Panel className="p-0 overflow-hidden">
        {logs.length === 0 ? (
          <EmptyState message="No audit events yet." testid="audit-empty" />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Action</TableHead>
                  <TableHead>Resource</TableHead>
                  <TableHead>Actor</TableHead>
                  <TableHead>Changes</TableHead>
                  <TableHead>Time</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {logs.map((l) => (
                  <TableRow key={l.id} data-testid={`audit-row-${l.id}`}>
                    <TableCell><span className="font-mono text-xs px-2 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">{l.action}</span></TableCell>
                    <TableCell className="font-mono text-xs">{l.resource_type}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{l.actor_email || "system"}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground max-w-[280px] truncate">{JSON.stringify(l.changes)}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{new Date(l.created_at).toLocaleString()}</TableCell>
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
