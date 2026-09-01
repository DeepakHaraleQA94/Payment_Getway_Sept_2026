import { useEffect, useState, useCallback } from "react";
import { ShieldCheck, KeyRound, Monitor, History, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, formatApiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, StatusBadge, EmptyState } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export default function Security() {
  const { user, checkAuth } = useAuth();
  const [me, setMe] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [history, setHistory] = useState([]);
  const [pw, setPw] = useState({ current: "", next: "" });
  const [setup, setSetup] = useState(null);
  const [mfaCode, setMfaCode] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [m, s, h] = await Promise.all([
      api.get("/auth/me"), api.get("/auth/sessions"), api.get("/auth/login-history"),
    ]);
    setMe(m.data); setSessions(s.data); setHistory(h.data);
  }, []);
  useEffect(() => { load(); }, [load]);

  const changePassword = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/auth/change-password", { current_password: pw.current, new_password: pw.next });
      toast.success("Password changed");
      setPw({ current: "", next: "" });
      load();
    } catch (err) { toast.error(formatApiError(err.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const startMfa = async () => {
    try { const { data } = await api.post("/auth/mfa/setup"); setSetup(data); }
    catch (err) { toast.error(formatApiError(err.response?.data?.detail)); }
  };
  const enableMfa = async () => {
    try { await api.post("/auth/mfa/enable", { code: mfaCode }); toast.success("MFA enabled"); setSetup(null); setMfaCode(""); load(); checkAuth(); }
    catch (err) { toast.error(formatApiError(err.response?.data?.detail)); }
  };
  const disableMfa = async () => {
    const code = window.prompt("Enter a current authenticator code to disable MFA:");
    if (!code) return;
    try { await api.post("/auth/mfa/disable", { code }); toast.success("MFA disabled"); load(); checkAuth(); }
    catch (err) { toast.error(formatApiError(err.response?.data?.detail)); }
  };
  const revoke = async (id) => { try { await api.delete(`/auth/sessions/${id}`); toast.success("Session revoked"); load(); } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); } };
  const revokeAll = async () => { try { await api.post("/auth/sessions/revoke-all"); toast.success("All sessions revoked. Sign in again."); } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); } };

  return (
    <div data-testid="security-page">
      <PageHeader title="Security" subtitle="Manage MFA, password, active sessions and login history." />
      {me?.mfa_enrollment_required && !me?.mfa_enabled && (
        <Panel className="mb-6 border-amber-500/30" data-testid="mfa-required-banner">
          <p className="text-sm text-amber-400">You are a privileged user — enabling MFA is strongly required for your account.</p>
        </Panel>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Panel data-testid="mfa-panel">
          <div className="flex items-center gap-2 mb-4"><ShieldCheck className="h-4 w-4 text-primary" /><h3 className="font-heading text-lg font-medium">Two-Factor Authentication</h3></div>
          <div className="mb-3"><StatusBadge status={me?.mfa_enabled ? "active" : "suspended"} /> <span className="text-sm text-muted-foreground ml-2">{me?.mfa_enabled ? "Enabled" : "Disabled"}</span></div>
          {me?.mfa_enabled ? (
            <Button variant="outline" data-testid="disable-mfa-button" onClick={disableMfa}>Disable MFA</Button>
          ) : setup ? (
            <div className="space-y-3">
              <p className="text-xs text-muted-foreground">Add this secret to your authenticator app (Google Authenticator, Authy):</p>
              <code data-testid="mfa-setup-secret" className="block text-xs break-all p-2 rounded bg-secondary/60 border border-border">{setup.secret}</code>
              <div className="space-y-2"><Label>Enter code to confirm</Label>
                <Input data-testid="mfa-enable-code-input" value={mfaCode} onChange={(e) => setMfaCode(e.target.value)} placeholder="123456" /></div>
              <Button data-testid="enable-mfa-button" onClick={enableMfa}>Enable MFA</Button>
            </div>
          ) : (
            <Button data-testid="setup-mfa-button" onClick={startMfa}>Set up MFA</Button>
          )}
        </Panel>

        <Panel data-testid="password-panel">
          <div className="flex items-center gap-2 mb-4"><KeyRound className="h-4 w-4 text-primary" /><h3 className="font-heading text-lg font-medium">Change Password</h3></div>
          <form onSubmit={changePassword} className="space-y-3">
            <div className="space-y-2"><Label>Current password</Label>
              <Input data-testid="current-password-input" type="password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} required /></div>
            <div className="space-y-2"><Label>New password</Label>
              <Input data-testid="new-password-input" type="password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} required minLength={8} /></div>
            <Button type="submit" data-testid="change-password-button" disabled={busy}>Update password</Button>
          </form>
        </Panel>
      </div>

      <Panel className="mt-6 p-0 overflow-hidden" data-testid="sessions-panel">
        <div className="px-5 py-4 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-2"><Monitor className="h-4 w-4 text-primary" /><h3 className="font-heading text-lg font-medium">Active Sessions</h3></div>
          <Button variant="ghost" size="sm" data-testid="revoke-all-button" onClick={revokeAll}><Trash2 className="h-3.5 w-3.5 mr-1" /> Revoke all</Button>
        </div>
        {sessions.length === 0 ? <EmptyState message="No active sessions." testid="sessions-empty" /> : (
          <div className="overflow-x-auto"><Table>
            <TableHeader><TableRow><TableHead>Type</TableHead><TableHead>IP</TableHead><TableHead>Device</TableHead><TableHead>Created</TableHead><TableHead className="text-right">Action</TableHead></TableRow></TableHeader>
            <TableBody>{sessions.map((s) => (
              <TableRow key={s.id} data-testid={`session-row-${s.id}`}>
                <TableCell className="font-mono text-xs">{s.kind}{s.current && <span className="ml-1 text-emerald-400">(current)</span>}</TableCell>
                <TableCell className="font-mono text-xs">{s.ip_address || "—"}</TableCell>
                <TableCell className="text-xs text-muted-foreground max-w-[240px] truncate">{s.user_agent || "—"}</TableCell>
                <TableCell className="font-mono text-xs text-muted-foreground">{new Date(s.created_at).toLocaleString()}</TableCell>
                <TableCell className="text-right"><Button variant="ghost" size="sm" data-testid={`revoke-session-${s.id}`} onClick={() => revoke(s.id)}>Revoke</Button></TableCell>
              </TableRow>
            ))}</TableBody>
          </Table></div>
        )}
      </Panel>

      <Panel className="mt-6 p-0 overflow-hidden" data-testid="login-history-panel">
        <div className="px-5 py-4 border-b border-border flex items-center gap-2"><History className="h-4 w-4 text-primary" /><h3 className="font-heading text-lg font-medium">Login History</h3></div>
        {history.length === 0 ? <EmptyState message="No login history." testid="history-empty" /> : (
          <div className="overflow-x-auto"><Table>
            <TableHeader><TableRow><TableHead>Result</TableHead><TableHead>Reason</TableHead><TableHead>IP</TableHead><TableHead>Time</TableHead></TableRow></TableHeader>
            <TableBody>{history.map((h) => (
              <TableRow key={h.id} data-testid={`history-row-${h.id}`}>
                <TableCell><StatusBadge status={h.success ? "succeeded" : "failed"} /></TableCell>
                <TableCell className="font-mono text-xs">{h.reason || "—"}</TableCell>
                <TableCell className="font-mono text-xs">{h.ip_address || "—"}</TableCell>
                <TableCell className="font-mono text-xs text-muted-foreground">{new Date(h.created_at).toLocaleString()}</TableCell>
              </TableRow>
            ))}</TableBody>
          </Table></div>
        )}
      </Panel>
    </div>
  );
}
