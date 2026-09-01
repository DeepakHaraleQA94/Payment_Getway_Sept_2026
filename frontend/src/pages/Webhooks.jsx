import { useEffect, useState, useCallback } from "react";
import { Plus, Trash2, Zap, Webhook as WebhookIcon, RefreshCw } from "lucide-react";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export default function Webhooks() {
  const { selectedTenantId } = useAuth();
  const [endpoints, setEndpoints] = useState([]);
  const [deliveries, setDeliveries] = useState([]);
  const [events, setEvents] = useState([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ url: "", description: "", events: [] });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const p = { params: { tenant_id: selectedTenantId } };
    const [e, d, ev] = await Promise.all([
      api.get("/webhooks/endpoints", p), api.get("/webhooks/deliveries", p), api.get("/webhooks/events"),
    ]);
    setEndpoints(e.data); setDeliveries(d.data); setEvents(ev.data.events);
  }, [selectedTenantId]);
  useEffect(() => { load(); }, [load]);

  const create = async () => {
    setBusy(true);
    try {
      await api.post("/webhooks/endpoints", form, { params: { tenant_id: selectedTenantId } });
      toast.success("Webhook endpoint added");
      setOpen(false); setForm({ url: "", description: "", events: [] }); load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const remove = async (id) => {
    try { await api.delete(`/webhooks/endpoints/${id}`); toast.success("Endpoint deleted"); load(); }
    catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };

  const test = async (id) => {
    try { await api.post(`/webhooks/endpoints/${id}/test`); toast.success("Test event dispatched"); load(); }
    catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };

  const replay = async (id) => {
    try { await api.post(`/webhooks/deliveries/${id}/replay`); toast.success("Delivery replayed (same event id)"); load(); }
    catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };

  const toggleEvent = (ev) => setForm((f) => ({
    ...f, events: f.events.includes(ev) ? f.events.filter((x) => x !== ev) : [...f.events, ev],
  }));

  return (
    <div data-testid="webhooks-page">
      <PageHeader
        title="Webhook Notifications"
        subtitle="Get notified on payment and refund events, with a live delivery inspector."
        action={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button data-testid="add-webhook-button"><Plus className="h-4 w-4 mr-2" /> Add Endpoint</Button>
            </DialogTrigger>
            <DialogContent data-testid="add-webhook-dialog">
              <DialogHeader>
                <DialogTitle>Add Webhook Endpoint</DialogTitle>
                <DialogDescription>We'll POST signed events to this URL. Leave events empty to receive all.</DialogDescription>
              </DialogHeader>
              <div className="space-y-4 py-2">
                <div className="space-y-2"><Label>Endpoint URL</Label>
                  <Input data-testid="webhook-url-input" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://your-site.com/webhooks/cloudpay" /></div>
                <div className="space-y-2"><Label>Description</Label>
                  <Input data-testid="webhook-desc-input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
                <div className="space-y-2">
                  <Label>Events</Label>
                  <div className="grid grid-cols-1 gap-1.5">
                    {events.map((ev) => (
                      <label key={ev} className="flex items-center gap-2 text-sm p-2 rounded hover:bg-secondary/60 cursor-pointer">
                        <input type="checkbox" data-testid={`webhook-event-${ev}`} checked={form.events.includes(ev)} onChange={() => toggleEvent(ev)} />
                        <span className="font-mono text-xs">{ev}</span>
                      </label>
                    ))}
                  </div>
                </div>
              </div>
              <DialogFooter><Button data-testid="submit-webhook-button" onClick={create} disabled={busy || !form.url}>Add</Button></DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />
      <Tabs defaultValue="deliveries">
        <TabsList data-testid="webhook-tabs">
          <TabsTrigger value="deliveries" data-testid="tab-deliveries">Delivery Inspector</TabsTrigger>
          <TabsTrigger value="endpoints" data-testid="tab-endpoints">Endpoints</TabsTrigger>
        </TabsList>

        <TabsContent value="deliveries" className="mt-4">
          <Panel className="p-0 overflow-hidden">
            {deliveries.length === 0 ? (
              <EmptyState message="No webhook events yet. Create a payment to see events here." testid="deliveries-empty" />
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Event</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Target</TableHead>
                      <TableHead>Code</TableHead>
                      <TableHead>Attempts</TableHead>
                      <TableHead>Next retry</TableHead>
                      <TableHead>Time</TableHead>
                      <TableHead className="text-right">Action</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {deliveries.map((d) => (
                      <TableRow key={d.id} data-testid={`delivery-row-${d.id}`}>
                        <TableCell>
                          <span className="font-mono text-xs px-2 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">{d.event}</span>
                          {d.is_replay && <span className="ml-1 text-[10px] font-mono text-indigo-400">replay</span>}
                        </TableCell>
                        <TableCell><StatusBadge status={d.status === "no_endpoint" ? "created" : d.status === "success" ? "succeeded" : d.status === "retrying" ? "pending" : d.status === "exhausted" ? "failed" : d.status} /></TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground max-w-[200px] truncate">{d.target_url || "no endpoint configured"}</TableCell>
                        <TableCell className="font-mono text-xs">{d.response_code ?? "—"}</TableCell>
                        <TableCell className="font-mono text-xs">{d.attempts}/{d.max_attempts}</TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">{d.next_attempt_at ? new Date(d.next_attempt_at).toLocaleTimeString() : "—"}</TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">{new Date(d.created_at).toLocaleString()}</TableCell>
                        <TableCell className="text-right">
                          {d.target_url && (
                            <Button variant="ghost" size="sm" data-testid={`replay-delivery-${d.id}`} onClick={() => replay(d.id)}>
                              <RefreshCw className="h-3.5 w-3.5 mr-1" /> Replay
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
        </TabsContent>

        <TabsContent value="endpoints" className="mt-4">
          {endpoints.length === 0 ? (
            <Panel><EmptyState message="No endpoints configured." testid="endpoints-empty" /></Panel>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6">
              {endpoints.map((ep) => (
                <Panel key={ep.id} data-testid={`endpoint-card-${ep.id}`}>
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="h-9 w-9 rounded-lg bg-primary/15 text-primary flex items-center justify-center shrink-0"><WebhookIcon className="h-4.5 w-4.5" /></div>
                      <div className="min-w-0"><p className="font-mono text-sm truncate">{ep.url}</p><p className="text-xs text-muted-foreground">{ep.description || "—"}</p></div>
                    </div>
                    <StatusBadge status={ep.enabled ? "active" : "suspended"} />
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {(ep.events?.length ? ep.events : ["all events"]).map((e) => (
                      <span key={e} className="text-xs font-mono px-2 py-0.5 rounded bg-secondary/60 border border-border">{e}</span>
                    ))}
                  </div>
                  <div className="mt-4 flex gap-2">
                    <Button size="sm" variant="outline" data-testid={`test-webhook-${ep.id}`} onClick={() => test(ep.id)}><Zap className="h-3.5 w-3.5 mr-1" /> Send test</Button>
                    <Button size="sm" variant="ghost" data-testid={`delete-webhook-${ep.id}`} onClick={() => remove(ep.id)}><Trash2 className="h-3.5 w-3.5 mr-1" /> Delete</Button>
                  </div>
                </Panel>
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
