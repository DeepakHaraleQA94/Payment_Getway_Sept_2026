import { useEffect, useState, useCallback } from "react";
import { Plus, Plug } from "lucide-react";
import { toast } from "sonner";
import { api, formatApiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, StatusBadge, EmptyState } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";

export default function Providers() {
  const { selectedTenantId } = useAuth();
  const [available, setAvailable] = useState([]);
  const [configured, setConfigured] = useState([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ provider_key: "mock", display_name: "", priority: 10 });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const [a, c] = await Promise.all([
      api.get("/providers/available"),
      api.get("/providers", { params: { tenant_id: selectedTenantId } }),
    ]);
    setAvailable(a.data);
    setConfigured(c.data);
  }, [selectedTenantId]);
  useEffect(() => { load(); }, [load]);

  const add = async () => {
    setBusy(true);
    try {
      const meta = available.find((p) => p.key === form.provider_key);
      await api.post("/providers", {
        provider_key: form.provider_key,
        display_name: form.display_name || meta?.display_name || form.provider_key,
        mode: "sandbox",
        enabled: true,
        priority: Number(form.priority) || 100,
        supported_currencies: meta?.supported_currencies || [],
      }, { params: { tenant_id: selectedTenantId } });
      toast.success("Provider adapter configured");
      setOpen(false);
      load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  return (
    <div data-testid="providers-page">
      <PageHeader
        title="Provider Adapters"
        subtitle="Pluggable payment providers. Live mode stays disabled until authorized."
        action={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button data-testid="add-provider-button"><Plus className="h-4 w-4 mr-2" /> Add Provider</Button>
            </DialogTrigger>
            <DialogContent data-testid="add-provider-dialog">
              <DialogHeader>
                <DialogTitle>Configure Provider Adapter</DialogTitle>
                <DialogDescription>Attach a sandbox payment provider adapter to this tenant.</DialogDescription>
              </DialogHeader>
              <div className="space-y-4 py-2">
                <div className="space-y-2">
                  <Label>Adapter</Label>
                  <Select value={form.provider_key} onValueChange={(v) => setForm({ ...form, provider_key: v })}>
                    <SelectTrigger data-testid="provider-select"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {available.map((p) => (
                        <SelectItem key={p.key} value={p.key}>{p.display_name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Display name</Label>
                  <Input data-testid="provider-name-input" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder="Optional" />
                </div>
                <div className="space-y-2">
                  <Label>Priority</Label>
                  <Input data-testid="provider-priority-input" type="number" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} />
                </div>
              </div>
              <DialogFooter><Button data-testid="submit-provider-button" onClick={add} disabled={busy}>Add</Button></DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />

      {configured.length === 0 ? (
        <Panel><EmptyState message="No providers configured for this tenant." testid="providers-empty" /></Panel>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-6">
          {configured.map((p) => (
            <Panel key={p.id} className="cp-anim" >
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div className="h-10 w-10 rounded-lg bg-primary/15 text-primary flex items-center justify-center"><Plug className="h-5 w-5" /></div>
                  <div>
                    <p className="font-medium">{p.display_name}</p>
                    <p className="text-xs font-mono text-muted-foreground">{p.provider_key}</p>
                  </div>
                </div>
                <StatusBadge status={p.enabled ? "active" : "suspended"} />
              </div>
              <div className="mt-4 flex items-center justify-between text-xs font-mono text-muted-foreground">
                <span>MODE: {p.mode.toUpperCase()}</span>
                <span>PRIORITY: {p.priority}</span>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(p.supported_currencies || []).map((c) => (
                  <span key={c} className="text-xs font-mono px-2 py-0.5 rounded bg-secondary/60 border border-border">{c}</span>
                ))}
              </div>
            </Panel>
          ))}
        </div>
      )}
    </div>
  );
}
