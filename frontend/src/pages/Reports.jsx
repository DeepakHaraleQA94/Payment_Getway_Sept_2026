import { useEffect, useState, useCallback } from "react";
import { Play, Download, FileText, Mail, Save } from "lucide-react";
import { toast } from "sonner";
import { api, formatApiError, downloadCsv } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, StatusBadge, EmptyState } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const FREQS = ["daily", "weekly", "monthly"];

export default function Reports() {
  const { selectedTenantId } = useAuth();
  const [reports, setReports] = useState([]);
  const [busy, setBusy] = useState(false);
  const [reportType, setReportType] = useState("daily");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [email, setEmail] = useState(null);
  const [savingEmail, setSavingEmail] = useState(false);

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const [rep, es] = await Promise.all([
      api.get("/reports/scheduled", { params: { tenant_id: selectedTenantId } }),
      api.get("/reports/scheduled/email-settings", { params: { tenant_id: selectedTenantId } }),
    ]);
    setReports(rep.data);
    setEmail(es.data);
  }, [selectedTenantId]);
  useEffect(() => { load(); }, [load]);

  const runNow = async () => {
    if (reportType === "custom" && (!startDate || !endDate)) {
      toast.error("Pick a start and end date for a custom report");
      return;
    }
    setBusy(true);
    try {
      const params = { tenant_id: selectedTenantId, report_type: reportType };
      if (reportType === "custom") { params.start_date = startDate; params.end_date = endDate; }
      await api.post("/reports/scheduled/run", {}, { params });
      toast.success(`${reportType[0].toUpperCase()}${reportType.slice(1)} report generated`);
      load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const saveEmail = async () => {
    setSavingEmail(true);
    try {
      const { data } = await api.put("/reports/scheduled/email-settings", {
        enabled: email.enabled,
        recipient_email: email.recipient_email || null,
        frequencies: email.frequencies || [],
        attach_csv: email.attach_csv,
      }, { params: { tenant_id: selectedTenantId } });
      setEmail(data);
      toast.success("Email report settings saved");
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
    finally { setSavingEmail(false); }
  };

  const toggleFreq = (f) => setEmail((s) => {
    const set = new Set(s.frequencies || []);
    set.has(f) ? set.delete(f) : set.add(f);
    return { ...s, frequencies: FREQS.filter((x) => set.has(x)) };
  });

  const download = (fileId) =>
    downloadCsv(`/reports/scheduled/download/${fileId}`, {}, `cloudpay-report-${fileId.slice(0, 8)}.csv`);

  return (
    <div data-testid="reports-page">
      <PageHeader
        title="Scheduled Reports"
        subtitle="Automatic payments & settlements summaries: daily at 08:00, weekly on Mondays, monthly on the 1st (UTC)."
        action={
          <div className="flex flex-wrap items-center gap-2">
            <Select value={reportType} onValueChange={setReportType}>
              <SelectTrigger className="w-32" data-testid="report-type-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="daily" data-testid="report-type-daily">Daily</SelectItem>
                <SelectItem value="weekly" data-testid="report-type-weekly">Weekly</SelectItem>
                <SelectItem value="monthly" data-testid="report-type-monthly">Monthly</SelectItem>
                <SelectItem value="custom" data-testid="report-type-custom">Custom range</SelectItem>
              </SelectContent>
            </Select>
            {reportType === "custom" && (
              <div className="flex items-center gap-1.5" data-testid="custom-range-inputs">
                <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)}
                  className="w-40" data-testid="report-start-date" />
                <span className="text-muted-foreground text-sm">→</span>
                <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)}
                  className="w-40" data-testid="report-end-date" />
              </div>
            )}
            <Button data-testid="run-report-button" onClick={runNow} disabled={busy}>
              <Play className="h-4 w-4 mr-2" /> Run report now
            </Button>
          </div>
        }
      />

      <Panel className="mb-6 flex items-start gap-3">
        <FileText className="h-5 w-5 text-primary mt-0.5" />
        <div>
          <p className="text-sm">Reports are generated and stored for in-app download. Daily covers a single day,
            weekly the trailing 7 days, monthly the previous calendar month, and custom any date range you pick.
            Email delivery uses a provider-agnostic adapter and is inactive until an email provider (e.g. Resend,
            SendGrid or SES) is connected.</p>
        </div>
      </Panel>

      {/* Email delivery settings — future-ready; stored per tenant, no provider connected yet */}
      {email && (
        <Panel className="mb-6" data-testid="email-settings-panel">
          <div className="flex items-center justify-between gap-3 mb-4">
            <div className="flex items-center gap-2">
              <Mail className="h-5 w-5 text-primary" />
              <h3 className="font-heading text-lg font-medium">Email delivery</h3>
              <Badge variant="outline" className="text-[10px] font-mono" data-testid="email-provider-status">
                no provider connected
              </Badge>
            </div>
            <Button size="sm" onClick={saveEmail} disabled={savingEmail} data-testid="save-email-settings-button">
              <Save className="h-4 w-4 mr-1.5" /> Save
            </Button>
          </div>
          <div className="grid gap-5 md:grid-cols-2">
            <div className="flex items-center justify-between rounded-lg border border-border p-3">
              <div>
                <Label className="text-sm">Enable email delivery</Label>
                <p className="text-xs text-muted-foreground mt-0.5">Queue reports for email once a provider is connected.</p>
              </div>
              <Switch checked={!!email.enabled} onCheckedChange={(v) => setEmail((s) => ({ ...s, enabled: v }))}
                data-testid="email-enabled-switch" />
            </div>
            <div className="rounded-lg border border-border p-3">
              <Label className="text-sm" htmlFor="recip">Recipient email</Label>
              <Input id="recip" type="email" placeholder="reports@yourdomain.com"
                value={email.recipient_email || ""}
                onChange={(e) => setEmail((s) => ({ ...s, recipient_email: e.target.value }))}
                className="mt-2" data-testid="email-recipient-input" />
            </div>
            <div className="rounded-lg border border-border p-3">
              <Label className="text-sm">Frequencies</Label>
              <div className="flex flex-wrap gap-4 mt-2">
                {FREQS.map((f) => (
                  <label key={f} className="flex items-center gap-2 text-sm cursor-pointer">
                    <Checkbox checked={(email.frequencies || []).includes(f)} onCheckedChange={() => toggleFreq(f)}
                      data-testid={`email-freq-${f}`} />
                    <span className="capitalize">{f}</span>
                  </label>
                ))}
              </div>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-border p-3">
              <div>
                <Label className="text-sm">Attach CSV file</Label>
                <p className="text-xs text-muted-foreground mt-0.5">Include the report CSV as an attachment.</p>
              </div>
              <Switch checked={email.attach_csv !== false}
                onCheckedChange={(v) => setEmail((s) => ({ ...s, attach_csv: v }))}
                data-testid="email-attach-switch" />
            </div>
          </div>
        </Panel>
      )}

      <Panel className="p-0 overflow-hidden">
        {reports.length === 0 ? (
          <EmptyState message="No reports yet. Click 'Run report now' to generate a report." testid="reports-empty" />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Period</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead className="text-right">Payments</TableHead>
                  <TableHead className="text-right">Settlements</TableHead>
                  <TableHead>Recipient</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead className="text-right">Download</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {reports.map((r) => (
                  <TableRow key={r.id} data-testid={`report-row-${r.id}`}>
                    <TableCell className="font-mono text-xs">{r.period_date}</TableCell>
                    <TableCell><StatusBadge status="active" />{" "}<span className="text-xs text-muted-foreground">{r.report_type}</span></TableCell>
                    <TableCell className="text-right font-mono">{r.payments_count}</TableCell>
                    <TableCell className="text-right font-mono">{r.settlements_count}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{r.recipient_email || "—"}</TableCell>
                    <TableCell><span className="text-xs font-mono text-amber-400">{r.email_status}</span></TableCell>
                    <TableCell className="text-right">
                      {r.file_id ? (
                        <Button variant="ghost" size="sm" data-testid={`download-report-${r.id}`} onClick={() => download(r.file_id)}>
                          <Download className="h-3.5 w-3.5 mr-1" /> CSV
                        </Button>
                      ) : <span className="text-xs text-muted-foreground">unavailable</span>}
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
