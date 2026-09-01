import { useEffect, useState, useCallback } from "react";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { api, formatApiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, EmptyState } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";

export default function Features() {
  const { selectedTenantId } = useAuth();
  const [flags, setFlags] = useState([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ key: "", name: "", description: "" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const { data } = await api.get("/features", { params: { tenant_id: selectedTenantId } });
    setFlags(data);
  }, [selectedTenantId]);
  useEffect(() => { load(); }, [load]);

  const toggle = async (flag) => {
    try {
      await api.patch(`/features/${flag.id}`, { enabled: !flag.enabled });
      setFlags((f) => f.map((x) => (x.id === flag.id ? { ...x, enabled: !x.enabled } : x)));
      toast.success(`${flag.name} ${!flag.enabled ? "enabled" : "disabled"}`);
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };

  const create = async () => {
    setBusy(true);
    try {
      await api.post("/features", {
        key: form.key.toLowerCase().replace(/[^a-z0-9_]/g, "_"),
        name: form.name || form.key,
        description: form.description,
        enabled: false,
      }, { params: { tenant_id: selectedTenantId } });
      toast.success("Feature flag created");
      setOpen(false); setForm({ key: "", name: "", description: "" }); load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  return (
    <div data-testid="features-page">
      <PageHeader
        title="Feature Flags"
        subtitle="Gate regulated and experimental capabilities per tenant."
        action={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild><Button data-testid="add-feature-button"><Plus className="h-4 w-4 mr-2" /> New Flag</Button></DialogTrigger>
            <DialogContent data-testid="add-feature-dialog">
              <DialogHeader>
                <DialogTitle>Create Feature Flag</DialogTitle>
                <DialogDescription>Gate a capability on or off per tenant.</DialogDescription>
              </DialogHeader>
              <div className="space-y-4 py-2">
                <div className="space-y-2"><Label>Key</Label>
                  <Input data-testid="feature-key-input" value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} placeholder="fx_conversion" /></div>
                <div className="space-y-2"><Label>Name</Label>
                  <Input data-testid="feature-name-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
                <div className="space-y-2"><Label>Description</Label>
                  <Input data-testid="feature-desc-input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
              </div>
              <DialogFooter><Button data-testid="submit-feature-button" onClick={create} disabled={busy || !form.key}>Create</Button></DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />
      {flags.length === 0 ? (
        <Panel><EmptyState message="No feature flags for this tenant." testid="features-empty" /></Panel>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6">
          {flags.map((f) => (
            <Panel key={f.id} className="flex items-start justify-between cp-anim" data-testid={`feature-card-${f.key}`}>
              <div className="pr-4">
                <p className="font-medium">{f.name}</p>
                <p className="text-xs font-mono text-muted-foreground mt-0.5">{f.key}</p>
                {f.description && <p className="text-sm text-muted-foreground mt-2">{f.description}</p>}
              </div>
              <Switch checked={f.enabled} onCheckedChange={() => toggle(f)} data-testid={`feature-toggle-${f.key}`} />
            </Panel>
          ))}
        </div>
      )}
    </div>
  );
}
