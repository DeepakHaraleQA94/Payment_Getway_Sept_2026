import { useEffect, useState, useCallback } from "react";
import { Play, Download, FileText } from "lucide-react";
import { toast } from "sonner";
import { api, formatApiError, downloadCsv } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, StatusBadge, EmptyState } from "@/components/common";
import { Button } from "@/components/ui/button";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export default function Reports() {
  const { selectedTenantId } = useAuth();
  const [reports, setReports] = useState([]);
  const [busy, setBusy] = useState(false);
  const [reportType, setReportType] = useState("daily");

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const { data } = await api.get("/reports/scheduled", { params: { tenant_id: selectedTenantId } });
    setReports(data);
  }, [selectedTenantId]);
  useEffect(() => { load(); }, [load]);

  const runNow = async () => {
    setBusy(true);
    try {
      await api.post("/reports/scheduled/run", {}, { params: { tenant_id: selectedTenantId, report_type: reportType } });
      toast.success(`${reportType[0].toUpperCase()}${reportType.slice(1)} report generated`);
      load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const download = (fileId) =>
    downloadCsv(`/reports/scheduled/download/${fileId}`, {}, `cloudpay-report-${fileId.slice(0, 8)}.csv`);

  return (
    <div data-testid="reports-page">
      <PageHeader
        title="Scheduled Reports"
        subtitle="Automatic payments & settlements summaries: daily at 08:00, weekly on Mondays, monthly on the 1st (UTC)."
        action={
          <div className="flex items-center gap-2">
            <Select value={reportType} onValueChange={setReportType}>
              <SelectTrigger className="w-32" data-testid="report-type-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="daily" data-testid="report-type-daily">Daily</SelectItem>
                <SelectItem value="weekly" data-testid="report-type-weekly">Weekly</SelectItem>
                <SelectItem value="monthly" data-testid="report-type-monthly">Monthly</SelectItem>
              </SelectContent>
            </Select>
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
            weekly the trailing 7 days, and monthly the previous calendar month. Email delivery uses a
            provider-agnostic adapter and is inactive until an email provider (e.g. Resend or SendGrid) is configured.</p>
          <p className="text-xs font-mono text-muted-foreground mt-1">Recipient on record: each tenant's contact email · Email status: skipped_no_provider</p>
        </div>
      </Panel>
      <Panel className="p-0 overflow-hidden">
        {reports.length === 0 ? (
          <EmptyState message="No reports yet. Click 'Run report now' to generate today's report." testid="reports-empty" />
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
