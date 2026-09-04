import { useEffect, useState, useCallback, useRef } from "react";
import { Plus, Copy, ExternalLink, Check, Upload, Palette } from "lucide-react";
import { toast } from "sonner";
import { api, money, formatApiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, StatusBadge, EmptyState, MethodBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";

export default function Checkout() {
  const { selectedTenantId, tenants, loadTenants } = useAuth();
  const [sessions, setSessions] = useState([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ amount: "", description: "", customer_email: "", method: "card" });
  const [createdUrl, setCreatedUrl] = useState(null);
  const [copiedId, setCopiedId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [accent, setAccent] = useState("#3B82F6");
  const [logoUrl, setLogoUrl] = useState(null);
  const fileRef = useRef(null);
  const tenant = tenants.find((t) => t.id === selectedTenantId);

  useEffect(() => {
    if (tenant) {
      setAccent(tenant.brand_accent || "#3B82F6");
      setLogoUrl(tenant.brand_logo_file_id ? `${process.env.REACT_APP_BACKEND_URL}/api/public/files/${tenant.brand_logo_file_id}` : null);
    }
  }, [tenant]);

  const saveAccent = async () => {
    try {
      await api.patch(`/tenants/${selectedTenantId}/branding`, { brand_accent: accent });
      toast.success("Accent color saved");
      loadTenants();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };

  const uploadLogo = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      const { data } = await api.post(`/tenants/${selectedTenantId}/logo`, fd, { headers: { "Content-Type": "multipart/form-data" } });
      setLogoUrl(`${process.env.REACT_APP_BACKEND_URL}${data.logo_url}?t=${Date.now()}`);
      toast.success("Logo uploaded");
      loadTenants();
    } catch (err) { toast.error(formatApiError(err.response?.data?.detail)); }
  };

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const { data } = await api.get("/checkout/sessions", { params: { tenant_id: selectedTenantId } });
    setSessions(data);
  }, [selectedTenantId]);
  useEffect(() => { load(); }, [load]);

  const checkoutUrl = (token) => `${window.location.origin}/checkout/${token}`;

  const create = async () => {
    setBusy(true);
    try {
      const isUpi = form.method === "demo_upi";
      const { data } = await api.post("/checkout/sessions", {
        amount_minor: Math.round(parseFloat(form.amount) * 100),
        currency: isUpi ? "INR" : (tenant?.default_currency || "USD"),
        provider_key: isUpi ? "demo_upi" : "mock",
        description: form.description || null,
        customer_email: form.customer_email || null,
      }, { params: { tenant_id: selectedTenantId } });
      setCreatedUrl(checkoutUrl(data.token));
      setForm({ amount: "", description: "", customer_email: "", method: "card" });
      load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const copy = (url, id) => {
    navigator.clipboard.writeText(url);
    setCopiedId(id);
    toast.success("Checkout link copied");
    setTimeout(() => setCopiedId(null), 1500);
  };

  const closeDialog = () => { setOpen(false); setCreatedUrl(null); };

  return (
    <div data-testid="checkout-page">
      <PageHeader
        title="Hosted Checkout"
        subtitle="Create shareable payment links your customers can pay on a hosted page."
        action={
          <Dialog open={open} onOpenChange={(v) => (v ? setOpen(true) : closeDialog())}>
            <DialogTrigger asChild>
              <Button data-testid="create-checkout-button"><Plus className="h-4 w-4 mr-2" /> New Checkout Link</Button>
            </DialogTrigger>
            <DialogContent data-testid="create-checkout-dialog">
              <DialogHeader>
                <DialogTitle>Create Checkout Link</DialogTitle>
                <DialogDescription>Generate a hosted sandbox checkout you can share with a customer.</DialogDescription>
              </DialogHeader>
              {!createdUrl ? (
                <div className="space-y-4 py-2">
                  <div className="space-y-2"><Label>Payment method</Label>
                    <Select value={form.method} onValueChange={(v) => setForm({ ...form, method: v })}>
                      <SelectTrigger data-testid="checkout-method-select"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="card" data-testid="checkout-method-card">Card (Mock sandbox)</SelectItem>
                        <SelectItem value="demo_upi" data-testid="checkout-method-demo_upi">Demo UPI (INR sandbox)</SelectItem>
                      </SelectContent>
                    </Select>
                    {form.method === "demo_upi" && (
                      <p className="text-xs text-muted-foreground">Creates an INR UPI checkout with app choices (PhonePe/GPay/Paytm) and a scannable QR.</p>
                    )}
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-2"><Label>Amount ({form.method === "demo_upi" ? "INR" : (tenant?.default_currency || "USD")})</Label>
                      <Input data-testid="checkout-amount-input" type="number" step="0.01" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} placeholder="25.00" /></div>
                    <div className="space-y-2"><Label>Customer email</Label>
                      <Input data-testid="checkout-email-input" value={form.customer_email} onChange={(e) => setForm({ ...form, customer_email: e.target.value })} /></div>
                  </div>
                  <div className="space-y-2"><Label>Description</Label>
                    <Input data-testid="checkout-desc-input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Pro plan" /></div>
                </div>
              ) : (
                <div className="space-y-3 py-2">
                  <p className="text-sm text-muted-foreground">Share this link with your customer:</p>
                  <div className="flex items-center gap-2 p-3 rounded-lg bg-secondary/60 border border-border">
                    <code data-testid="checkout-created-url" className="text-xs break-all flex-1">{createdUrl}</code>
                    <Button size="sm" variant="ghost" data-testid="copy-checkout-url" onClick={() => copy(createdUrl, "new")}>
                      {copiedId === "new" ? <Check className="h-4 w-4 text-emerald-400" /> : <Copy className="h-4 w-4" />}
                    </Button>
                  </div>
                  <a href={createdUrl} target="_blank" rel="noreferrer" className="inline-flex items-center text-sm text-primary hover:underline">
                    Open checkout page <ExternalLink className="h-3.5 w-3.5 ml-1" />
                  </a>
                </div>
              )}
              <DialogFooter>
                {!createdUrl
                  ? <Button data-testid="submit-checkout-button" onClick={create} disabled={busy || !form.amount}>Create Link</Button>
                  : <Button data-testid="done-checkout-button" onClick={closeDialog}>Done</Button>}
              </DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />
      <Panel className="mb-6" data-testid="branding-panel">
        <div className="flex items-center gap-2 mb-4">
          <Palette className="h-4 w-4 text-primary" />
          <h3 className="font-heading text-lg font-medium">Checkout Branding</h3>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <Label className="mb-2 block">Logo</Label>
            <div className="flex items-center gap-3">
              <div className="h-14 w-14 rounded-lg border border-border bg-secondary/40 flex items-center justify-center overflow-hidden">
                {logoUrl ? <img src={logoUrl} alt="logo" data-testid="branding-logo-preview" className="h-full w-full object-contain" />
                  : <span className="text-xs text-muted-foreground">None</span>}
              </div>
              <input ref={fileRef} type="file" accept="image/*" className="hidden" data-testid="logo-file-input" onChange={uploadLogo} />
              <Button variant="outline" data-testid="upload-logo-button" onClick={() => fileRef.current?.click()}>
                <Upload className="h-4 w-4 mr-2" /> Upload logo
              </Button>
            </div>
            <p className="text-xs text-muted-foreground mt-2">PNG, JPG, SVG or WEBP up to 2MB.</p>
          </div>
          <div>
            <Label className="mb-2 block">Accent color</Label>
            <div className="flex items-center gap-3">
              <input type="color" value={accent} onChange={(e) => setAccent(e.target.value)} data-testid="accent-color-input"
                className="h-11 w-14 rounded-md bg-transparent border border-border cursor-pointer" />
              <Input value={accent} onChange={(e) => setAccent(e.target.value)} data-testid="accent-hex-input" className="font-mono w-32" />
              <Button data-testid="save-accent-button" onClick={saveAccent}>Save</Button>
            </div>
            <p className="text-xs text-muted-foreground mt-2">Applied to the hosted checkout page button and logo.</p>
          </div>
        </div>
      </Panel>
      <Panel className="p-0 overflow-hidden">
        {sessions.length === 0 ? (
          <EmptyState message="No checkout sessions yet." testid="checkout-empty" />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Reference</TableHead>
                  <TableHead>Method</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">Link</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sessions.map((s) => (
                  <TableRow key={s.id} data-testid={`checkout-row-${s.reference}`}>
                    <TableCell className="font-mono text-xs">{s.reference}</TableCell>
                    <TableCell><MethodBadge method={s.provider_key === "demo_upi" ? "upi" : "card"} testid={`checkout-method-badge-${s.reference}`} /></TableCell>
                    <TableCell className="text-right font-mono">{money(s.amount_minor, s.currency)}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{s.description || "—"}</TableCell>
                    <TableCell><StatusBadge status={s.status} /></TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{new Date(s.created_at).toLocaleString()}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm" data-testid={`copy-link-${s.reference}`} onClick={() => copy(checkoutUrl(s.token), s.id)}>
                        {copiedId === s.id ? <Check className="h-3.5 w-3.5 mr-1 text-emerald-400" /> : <Copy className="h-3.5 w-3.5 mr-1" />} Copy
                      </Button>
                    </TableCell>
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
