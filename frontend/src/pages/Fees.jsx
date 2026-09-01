import { useEffect, useState, useCallback } from "react";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { api, formatApiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, EmptyState } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export default function Fees() {
  const { selectedTenantId } = useAuth();
  const [rules, setRules] = useState([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", percent: "2.9", fixed: "0.30", min: "0", currency: "USD" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const { data } = await api.get("/fees", { params: { tenant_id: selectedTenantId } });
    setRules(data);
  }, [selectedTenantId]);
  useEffect(() => { load(); }, [load]);

  const add = async () => {
    setBusy(true);
    try {
      await api.post("/fees", {
        name: form.name || "Custom Fee",
        provider_key: "mock",
        currency: form.currency || null,
        percent_bps: Math.round(parseFloat(form.percent) * 100),
        fixed_minor: Math.round(parseFloat(form.fixed) * 100),
        min_fee_minor: Math.round(parseFloat(form.min) * 100),
        priority: 10,
      }, { params: { tenant_id: selectedTenantId } });
      toast.success("Fee rule created");
      setOpen(false);
      load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  return (
    <div data-testid="fees-page">
      <PageHeader
        title="Fee Engine"
        subtitle="Percentage + fixed fee rules applied per provider and currency."
        action={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button data-testid="add-fee-button"><Plus className="h-4 w-4 mr-2" /> New Rule</Button>
            </DialogTrigger>
            <DialogContent data-testid="add-fee-dialog">
              <DialogHeader>
                <DialogTitle>Create Fee Rule</DialogTitle>
                <DialogDescription>Define percentage and fixed fees applied by the fee engine.</DialogDescription>
              </DialogHeader>
              <div className="space-y-4 py-2">
                <div className="space-y-2"><Label>Name</Label>
                  <Input data-testid="fee-name-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Standard Card Fee" /></div>
                <div className="grid grid-cols-3 gap-3">
                  <div className="space-y-2"><Label>Percent %</Label>
                    <Input data-testid="fee-percent-input" type="number" step="0.01" value={form.percent} onChange={(e) => setForm({ ...form, percent: e.target.value })} /></div>
                  <div className="space-y-2"><Label>Fixed</Label>
                    <Input data-testid="fee-fixed-input" type="number" step="0.01" value={form.fixed} onChange={(e) => setForm({ ...form, fixed: e.target.value })} /></div>
                  <div className="space-y-2"><Label>Min</Label>
                    <Input data-testid="fee-min-input" type="number" step="0.01" value={form.min} onChange={(e) => setForm({ ...form, min: e.target.value })} /></div>
                </div>
                <div className="space-y-2"><Label>Currency</Label>
                  <Input data-testid="fee-currency-input" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value.toUpperCase() })} /></div>
              </div>
              <DialogFooter><Button data-testid="submit-fee-button" onClick={add} disabled={busy}>Create</Button></DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />
      <Panel className="p-0 overflow-hidden">
        {rules.length === 0 ? (
          <EmptyState message="No fee rules configured." testid="fees-empty" />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Provider</TableHead>
                  <TableHead>Currency</TableHead>
                  <TableHead className="text-right">Percent</TableHead>
                  <TableHead className="text-right">Fixed</TableHead>
                  <TableHead className="text-right">Min</TableHead>
                  <TableHead className="text-right">Priority</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rules.map((r) => (
                  <TableRow key={r.id} data-testid={`fee-row-${r.id}`}>
                    <TableCell className="font-medium">{r.name}</TableCell>
                    <TableCell className="font-mono text-xs">{r.provider_key || "any"}</TableCell>
                    <TableCell className="font-mono text-xs">{r.currency || "any"}</TableCell>
                    <TableCell className="text-right font-mono">{(r.percent_bps / 100).toFixed(2)}%</TableCell>
                    <TableCell className="text-right font-mono">{(r.fixed_minor / 100).toFixed(2)}</TableCell>
                    <TableCell className="text-right font-mono">{(r.min_fee_minor / 100).toFixed(2)}</TableCell>
                    <TableCell className="text-right font-mono">{r.priority}</TableCell>
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
