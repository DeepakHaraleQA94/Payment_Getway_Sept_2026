import { useEffect, useState, useCallback } from "react";
import { Plus, Building2 } from "lucide-react";
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

export default function Tenants() {
  const { tenants, loadTenants } = useAuth();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", slug: "", default_currency: "USD", country: "US", contact_email: "" });
  const [busy, setBusy] = useState(false);

  useEffect(() => { loadTenants(); }, [loadTenants]);

  const create = async () => {
    setBusy(true);
    try {
      await api.post("/tenants", {
        name: form.name,
        slug: form.slug.toLowerCase().replace(/[^a-z0-9-]/g, "-"),
        default_currency: form.currency || "USD",
        country: form.country || null,
        contact_email: form.contact_email || null,
      });
      toast.success("Tenant created");
      setOpen(false);
      setForm({ name: "", slug: "", default_currency: "USD", country: "US", contact_email: "" });
      loadTenants();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  return (
    <div data-testid="tenants-page">
      <PageHeader
        title="Tenants & Clients"
        subtitle="Isolated client accounts. Each tenant has its own providers, fees and ledger."
        action={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button data-testid="add-tenant-button"><Plus className="h-4 w-4 mr-2" /> New Tenant</Button>
            </DialogTrigger>
            <DialogContent data-testid="add-tenant-dialog">
              <DialogHeader>
                <DialogTitle>Create Tenant</DialogTitle>
                <DialogDescription>Provision an isolated client account with its own providers, fees and ledger.</DialogDescription>
              </DialogHeader>
              <div className="space-y-4 py-2">
                <div className="space-y-2"><Label>Name</Label>
                  <Input data-testid="tenant-name-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Acme Commerce" /></div>
                <div className="space-y-2"><Label>Slug</Label>
                  <Input data-testid="tenant-slug-input" value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} placeholder="acme" /></div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-2"><Label>Currency</Label>
                    <Input data-testid="tenant-currency-input" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value.toUpperCase() })} /></div>
                  <div className="space-y-2"><Label>Country</Label>
                    <Input data-testid="tenant-country-input" value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value.toUpperCase() })} /></div>
                </div>
                <div className="space-y-2"><Label>Contact email</Label>
                  <Input data-testid="tenant-email-input" value={form.contact_email} onChange={(e) => setForm({ ...form, contact_email: e.target.value })} /></div>
              </div>
              <DialogFooter><Button data-testid="submit-tenant-button" onClick={create} disabled={busy || !form.name || !form.slug}>Create</Button></DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />
      {tenants.length === 0 ? (
        <Panel><EmptyState message="No tenants yet." testid="tenants-empty" /></Panel>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-6">
          {tenants.map((t) => (
            <Panel key={t.id} className="cp-anim" >
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div className="h-10 w-10 rounded-lg bg-indigo-500/15 text-indigo-400 flex items-center justify-center"><Building2 className="h-5 w-5" /></div>
                  <div>
                    <p className="font-medium">{t.name}</p>
                    <p className="text-xs font-mono text-muted-foreground">{t.slug}</p>
                  </div>
                </div>
                <StatusBadge status={t.status} />
              </div>
              <div className="mt-4 grid grid-cols-2 gap-2 text-xs font-mono text-muted-foreground">
                <span>CCY: {t.default_currency}</span>
                <span>REGION: {t.country || "—"}</span>
                {t.is_platform && <span className="col-span-2 text-primary">PLATFORM TENANT</span>}
              </div>
            </Panel>
          ))}
        </div>
      )}
    </div>
  );
}
