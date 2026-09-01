import { useEffect, useState, useCallback } from "react";
import { Plus, Copy, Trash2, KeyRound, Check } from "lucide-react";
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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export default function ApiKeys() {
  const { selectedTenantId } = useAuth();
  const [keys, setKeys] = useState([]);
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [newSecret, setNewSecret] = useState(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const { data } = await api.get("/api-keys", { params: { tenant_id: selectedTenantId } });
    setKeys(data);
  }, [selectedTenantId]);
  useEffect(() => { load(); }, [load]);

  const create = async () => {
    setBusy(true);
    try {
      const { data } = await api.post("/api-keys", { label: label || "Default" }, { params: { tenant_id: selectedTenantId } });
      setNewSecret(data.secret);
      setLabel("");
      load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const revoke = async (id) => {
    try {
      await api.delete(`/api-keys/${id}`);
      toast.success("API key revoked");
      load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };

  const copy = () => {
    navigator.clipboard.writeText(newSecret);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const closeDialog = () => { setOpen(false); setNewSecret(null); };

  return (
    <div data-testid="apikeys-page">
      <PageHeader
        title="API Keys"
        subtitle="Secret keys let this tenant create checkout sessions from their own site."
        action={
          <Dialog open={open} onOpenChange={(v) => (v ? setOpen(true) : closeDialog())}>
            <DialogTrigger asChild>
              <Button data-testid="create-apikey-button"><Plus className="h-4 w-4 mr-2" /> Create Key</Button>
            </DialogTrigger>
            <DialogContent data-testid="create-apikey-dialog">
              <DialogHeader>
                <DialogTitle>Create API Key</DialogTitle>
                <DialogDescription>The secret is shown only once — copy and store it securely.</DialogDescription>
              </DialogHeader>
              {!newSecret ? (
                <div className="space-y-4 py-2">
                  <div className="space-y-2"><Label>Label</Label>
                    <Input data-testid="apikey-label-input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Production site" /></div>
                </div>
              ) : (
                <div className="space-y-3 py-2">
                  <p className="text-sm text-muted-foreground">Copy this secret now. You won't see it again.</p>
                  <div className="flex items-center gap-2 p-3 rounded-lg bg-secondary/60 border border-border">
                    <code data-testid="apikey-secret-value" className="text-xs break-all flex-1">{newSecret}</code>
                    <Button size="sm" variant="ghost" data-testid="copy-apikey-button" onClick={copy}>
                      {copied ? <Check className="h-4 w-4 text-emerald-400" /> : <Copy className="h-4 w-4" />}
                    </Button>
                  </div>
                </div>
              )}
              <DialogFooter>
                {!newSecret
                  ? <Button data-testid="submit-apikey-button" onClick={create} disabled={busy}>Generate</Button>
                  : <Button data-testid="done-apikey-button" onClick={closeDialog}>Done</Button>}
              </DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />
      <Panel className="p-0 overflow-hidden">
        {keys.length === 0 ? (
          <EmptyState message="No API keys yet. Create one to accept payments from your site." testid="apikeys-empty" />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Label</TableHead>
                  <TableHead>Key</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Last used</TableHead>
                  <TableHead className="text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {keys.map((k) => (
                  <TableRow key={k.id} data-testid={`apikey-row-${k.id}`}>
                    <TableCell className="font-medium flex items-center gap-2"><KeyRound className="h-4 w-4 text-primary" /> {k.label}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{k.key_prefix}_••••{k.last4}</TableCell>
                    <TableCell><StatusBadge status={k.active ? "active" : "suspended"} /></TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">{k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "—"}</TableCell>
                    <TableCell className="text-right">
                      {k.active && (
                        <Button variant="ghost" size="sm" data-testid={`revoke-apikey-${k.id}`} onClick={() => revoke(k.id)}>
                          <Trash2 className="h-3.5 w-3.5 mr-1" /> Revoke
                        </Button>
                      )}
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
