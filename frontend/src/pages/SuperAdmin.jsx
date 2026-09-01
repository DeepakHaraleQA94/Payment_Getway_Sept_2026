import { useEffect, useState, useCallback } from "react";
import { NavLink, useNavigate, Navigate } from "react-router-dom";
import { Loader2, ShieldCheck, Users, Building2, ToggleLeft, KeyRound, LayoutDashboard, LogOut, ArrowLeft, Plus, Save } from "lucide-react";
import { toast } from "sonner";
import { api, formatApiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, StatusBadge, EmptyState } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from "@/components/ui/dialog";

const SA_NAV = [
  { to: "/superadmin", label: "Overview", icon: LayoutDashboard, testid: "sa-nav-overview", end: true },
  { to: "/superadmin/admins", label: "Platform Admins", icon: Users, testid: "sa-nav-admins" },
  { to: "/superadmin/tenants", label: "Customers / Tenants", icon: Building2, testid: "sa-nav-tenants" },
  { to: "/superadmin/features", label: "Feature Control", icon: ToggleLeft, testid: "sa-nav-features" },
  { to: "/superadmin/roles", label: "Roles & Permissions", icon: KeyRound, testid: "sa-nav-roles" },
];

function SALayout({ children }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  return (
    <div className="min-h-screen flex bg-background" data-testid="superadmin-shell">
      <aside className="hidden md:flex flex-col w-64 border-r border-border shrink-0 h-screen sticky top-0">
        <div className="flex items-center gap-2.5 px-5 h-16 border-b border-border">
          <div className="h-8 w-8 rounded-lg bg-primary flex items-center justify-center">
            <ShieldCheck className="h-5 w-5 text-white" strokeWidth={2.5} />
          </div>
          <div className="leading-tight">
            <span className="font-heading text-base font-bold tracking-tight block">CloudPay</span>
            <span className="text-[10px] font-mono uppercase tracking-wider text-primary">Super Admin</span>
          </div>
        </div>
        <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-1">
          {SA_NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} data-testid={item.testid}
              className={({ isActive }) => `flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${isActive ? "bg-primary/15 text-primary font-medium" : "text-muted-foreground hover:text-foreground hover:bg-secondary/60"}`}>
              <item.icon className="h-[18px] w-[18px]" />{item.label}
            </NavLink>
          ))}
        </nav>
        <div className="p-3 border-t border-border space-y-1">
          <Button variant="ghost" size="sm" data-testid="sa-back-dashboard" onClick={() => navigate("/dashboard")} className="w-full justify-start text-muted-foreground">
            <ArrowLeft className="h-4 w-4 mr-2" /> Back to Dashboard
          </Button>
          <div className="px-2 py-1">
            <p className="text-sm font-medium truncate">{user?.name || user?.email}</p>
            <p className="text-xs font-mono text-primary">super admin</p>
          </div>
          <Button variant="ghost" size="sm" data-testid="sa-logout" onClick={async () => { await logout(); navigate("/login"); }} className="w-full justify-start text-muted-foreground">
            <LogOut className="h-4 w-4 mr-2" /> Sign out
          </Button>
        </div>
      </aside>
      <main className="flex-1 p-4 sm:p-6 lg:p-8 max-w-[1400px] w-full mx-auto">{children}</main>
    </div>
  );
}

export function SuperAdminGuard({ children }) {
  const { user } = useAuth();
  if (user === null) return <div className="min-h-screen flex items-center justify-center"><Loader2 className="h-8 w-8 animate-spin text-primary" /></div>;
  if (user === false) return <Navigate to="/login" replace />;
  if (!user.is_superadmin) return <Navigate to="/dashboard" replace />;
  return <SALayout>{children}</SALayout>;
}

// ----------------------------- Overview -----------------------------
export function SAOverview() {
  const [stats, setStats] = useState(null);
  useEffect(() => { api.get("/superadmin/overview").then(({ data }) => setStats(data)).catch(() => {}); }, []);
  const cards = [
    { key: "tenants", label: "Customer Tenants" },
    { key: "platform_admins", label: "Platform Admins" },
    { key: "super_admins", label: "Super Admins" },
    { key: "provider_accounts", label: "Provider Accounts" },
    { key: "payments", label: "Payments" },
  ];
  return (
    <div data-testid="sa-overview">
      <PageHeader title="Super Admin Overview" subtitle="Platform-wide control plane. Manage platform admins, customers, features and roles." />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {cards.map((c) => (
          <Panel key={c.key} data-testid={`sa-stat-${c.key}`}>
            <p className="text-xs font-mono uppercase tracking-wider text-muted-foreground">{c.label}</p>
            <p className="text-3xl font-heading font-semibold mt-1 tabular-nums">{stats ? stats[c.key] : "—"}</p>
          </Panel>
        ))}
      </div>
    </div>
  );
}

// ----------------------------- Platform Admins -----------------------------
export function SAAdmins() {
  const [admins, setAdmins] = useState([]);
  const [perms, setPerms] = useState([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ email: "", name: "", password: "", permission_codes: [] });
  const [editing, setEditing] = useState(null); // admin being permission-edited
  const [pwTarget, setPwTarget] = useState(null);
  const [newPw, setNewPw] = useState("");

  const load = useCallback(async () => {
    const [a, p] = await Promise.all([api.get("/superadmin/admins"), api.get("/permissions")]);
    setAdmins(a.data); setPerms(p.data);
  }, []);
  useEffect(() => { load(); }, [load]);

  const toggleCode = (list, code, setter) => setter(list.includes(code) ? list.filter((c) => c !== code) : [...list, code]);

  const create = async () => {
    try {
      await api.post("/superadmin/admins", form);
      toast.success("Platform admin created"); setOpen(false);
      setForm({ email: "", name: "", password: "", permission_codes: [] }); load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };
  const savePerms = async () => {
    try {
      await api.patch(`/superadmin/admins/${editing.id}`, { permission_codes: editing.permissions.filter((c) => c !== "*") });
      toast.success("Permissions updated"); setEditing(null); load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };
  const setStatus = async (a, status) => {
    try { await api.patch(`/superadmin/admins/${a.id}`, { status }); toast.success(`Admin ${status}`); load(); }
    catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };
  const savePassword = async () => {
    try { await api.post(`/superadmin/admins/${pwTarget.id}/set-password`, { password: newPw }); toast.success("Password updated"); setPwTarget(null); setNewPw(""); }
    catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };

  return (
    <div data-testid="sa-admins">
      <PageHeader title="Platform Admins" subtitle="Level-2 operational admins. They get exactly the permissions you grant — never Super Admin."
        action={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild><Button data-testid="sa-add-admin-btn"><Plus className="h-4 w-4 mr-2" />New Admin</Button></DialogTrigger>
            <DialogContent className="max-w-lg">
              <DialogHeader><DialogTitle>Create Platform Admin</DialogTitle></DialogHeader>
              <div className="space-y-3">
                <div><Label>Email</Label><Input type="email" data-testid="sa-admin-email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></div>
                <div><Label>Name</Label><Input data-testid="sa-admin-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
                <div><Label>Temporary Password</Label><Input type="password" data-testid="sa-admin-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></div>
                <div>
                  <Label>Granted Permissions</Label>
                  <div className="grid grid-cols-2 gap-2 mt-2 max-h-48 overflow-y-auto">
                    {perms.map((p) => (
                      <label key={p.code} className="flex items-center gap-2 text-xs cursor-pointer">
                        <Checkbox data-testid={`sa-perm-${p.code}`} checked={form.permission_codes.includes(p.code)}
                          onCheckedChange={() => toggleCode(form.permission_codes, p.code, (v) => setForm({ ...form, permission_codes: v }))} />
                        <span className="font-mono">{p.code}</span>
                      </label>
                    ))}
                  </div>
                </div>
              </div>
              <DialogFooter><Button data-testid="sa-admin-create-submit" onClick={create}>Create Admin</Button></DialogFooter>
            </DialogContent>
          </Dialog>
        } />
      <Panel className="p-0 overflow-hidden">
        {admins.length === 0 ? <EmptyState message="No platform admins yet." testid="sa-admins-empty" /> : (
          <Table>
            <TableHeader><TableRow><TableHead>Email</TableHead><TableHead>Name</TableHead><TableHead>Status</TableHead><TableHead>Permissions</TableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
            <TableBody>
              {admins.map((a) => (
                <TableRow key={a.id} data-testid={`sa-admin-row-${a.id}`}>
                  <TableCell className="font-mono text-xs">{a.email}</TableCell>
                  <TableCell>{a.name}</TableCell>
                  <TableCell><StatusBadge status={a.status} />{a.is_superadmin && <Badge className="ml-1 text-[10px]" variant="outline">super</Badge>}</TableCell>
                  <TableCell className="max-w-[280px]"><span className="text-[11px] font-mono text-muted-foreground">{a.is_superadmin ? "* (full)" : (a.permissions.join(", ") || "none")}</span></TableCell>
                  <TableCell className="text-right space-x-1">
                    {!a.is_superadmin && <>
                      <Button size="sm" variant="ghost" data-testid={`sa-edit-perms-${a.id}`} onClick={() => setEditing({ ...a, permissions: [...a.permissions] })}>Permissions</Button>
                      <Button size="sm" variant="ghost" data-testid={`sa-set-pw-${a.id}`} onClick={() => setPwTarget(a)}>Password</Button>
                      <Button size="sm" variant="ghost" data-testid={`sa-status-${a.id}`} onClick={() => setStatus(a, a.status === "active" ? "suspended" : "active")}>{a.status === "active" ? "Suspend" : "Activate"}</Button>
                    </>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Panel>

      <Dialog open={!!editing} onOpenChange={(v) => !v && setEditing(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader><DialogTitle>Edit Permissions {editing ? `— ${editing.email}` : ""}</DialogTitle></DialogHeader>
          <div className="grid grid-cols-2 gap-2 max-h-72 overflow-y-auto">
            {editing && perms.map((p) => (
              <label key={p.code} className="flex items-center gap-2 text-xs cursor-pointer">
                <Checkbox data-testid={`sa-editperm-${p.code}`} checked={editing.permissions.includes(p.code)}
                  onCheckedChange={() => setEditing({ ...editing, permissions: editing.permissions.includes(p.code) ? editing.permissions.filter((c) => c !== p.code) : [...editing.permissions, p.code] })} />
                <span className="font-mono">{p.code}</span>
              </label>
            ))}
          </div>
          <DialogFooter><Button data-testid="sa-save-perms" onClick={savePerms}><Save className="h-4 w-4 mr-1" />Save</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!pwTarget} onOpenChange={(v) => !v && setPwTarget(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader><DialogTitle>Set Password {pwTarget ? `— ${pwTarget.email}` : ""}</DialogTitle></DialogHeader>
          <Input type="password" placeholder="New password (min 8 chars)" data-testid="sa-new-password" value={newPw} onChange={(e) => setNewPw(e.target.value)} />
          <DialogFooter><Button data-testid="sa-save-password" onClick={savePassword} disabled={newPw.length < 8}>Update Password</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ----------------------------- Tenants -----------------------------
export function SATenants() {
  const [tenants, setTenants] = useState([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", slug: "", country: "", default_currency: "USD", contact_email: "" });
  const load = useCallback(async () => { const { data } = await api.get("/tenants"); setTenants(data); }, []);
  useEffect(() => { load(); }, [load]);
  const create = async () => {
    try { await api.post("/tenants", { ...form, contact_email: form.contact_email || null, country: form.country || null }); toast.success("Tenant created"); setOpen(false); setForm({ name: "", slug: "", country: "", default_currency: "USD", contact_email: "" }); load(); }
    catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };
  const setStatus = async (t, status) => {
    try { await api.patch(`/tenants/${t.id}`, { status }); toast.success(`Tenant ${status}`); load(); }
    catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };
  return (
    <div data-testid="sa-tenants">
      <PageHeader title="Customers / Tenants" subtitle="Create and manage customer tenants. Suspend to immediately block a customer."
        action={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild><Button data-testid="sa-add-tenant-btn"><Plus className="h-4 w-4 mr-2" />New Tenant</Button></DialogTrigger>
            <DialogContent className="max-w-md">
              <DialogHeader><DialogTitle>Create Tenant</DialogTitle></DialogHeader>
              <div className="space-y-3">
                <div><Label>Name</Label><Input data-testid="sa-tenant-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
                <div><Label>Slug</Label><Input data-testid="sa-tenant-slug" value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} /></div>
                <div className="grid grid-cols-2 gap-2">
                  <div><Label>Country</Label><Input data-testid="sa-tenant-country" placeholder="US" value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value.toUpperCase() })} /></div>
                  <div><Label>Currency</Label><Input data-testid="sa-tenant-currency" value={form.default_currency} onChange={(e) => setForm({ ...form, default_currency: e.target.value.toUpperCase() })} /></div>
                </div>
                <div><Label>Contact Email</Label><Input type="email" data-testid="sa-tenant-email" value={form.contact_email} onChange={(e) => setForm({ ...form, contact_email: e.target.value })} /></div>
              </div>
              <DialogFooter><Button data-testid="sa-tenant-create-submit" onClick={create}>Create Tenant</Button></DialogFooter>
            </DialogContent>
          </Dialog>
        } />
      <Panel className="p-0 overflow-hidden">
        <Table>
          <TableHeader><TableRow><TableHead>Name</TableHead><TableHead>Slug</TableHead><TableHead>Country</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
          <TableBody>
            {tenants.map((t) => (
              <TableRow key={t.id} data-testid={`sa-tenant-row-${t.slug}`}>
                <TableCell>{t.name}{t.is_platform && <Badge className="ml-2 text-[10px]" variant="outline">platform</Badge>}</TableCell>
                <TableCell className="font-mono text-xs">{t.slug}</TableCell>
                <TableCell>{t.country || "—"}</TableCell>
                <TableCell><StatusBadge status={t.status} /></TableCell>
                <TableCell className="text-right">
                  {!t.is_platform && <Button size="sm" variant="ghost" data-testid={`sa-tenant-status-${t.slug}`} onClick={() => setStatus(t, t.status === "active" ? "suspended" : "active")}>{t.status === "active" ? "Suspend" : "Activate"}</Button>}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Panel>
    </div>
  );
}

// ----------------------------- Feature Control -----------------------------
export function SAFeatures() {
  const [tenants, setTenants] = useState([]);
  const [tenantId, setTenantId] = useState("");
  const [features, setFeatures] = useState([]);
  useEffect(() => { api.get("/tenants").then(({ data }) => { const list = data.filter((t) => !t.is_platform); setTenants(list); if (list[0]) setTenantId(list[0].id); }); }, []);
  const load = useCallback(async () => { if (!tenantId) return; const { data } = await api.get("/superadmin/features", { params: { tenant_id: tenantId } }); setFeatures(data); }, [tenantId]);
  useEffect(() => { load(); }, [load]);
  const toggle = async (f, enabled) => {
    try { await api.put("/superadmin/features", { tenant_id: tenantId, key: f.key, name: f.name, enabled }); toast.success(`${f.name} ${enabled ? "enabled" : "disabled"}`); load(); }
    catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };
  return (
    <div data-testid="sa-features">
      <PageHeader title="Customer Feature Control" subtitle="Enable or disable features per customer. Disabling blocks the feature in their API and hides it in their dashboard." />
      <Panel className="mb-4 flex items-center gap-3">
        <Label className="text-sm">Customer</Label>
        <Select value={tenantId} onValueChange={setTenantId}>
          <SelectTrigger className="w-[260px]" data-testid="sa-feature-tenant-select"><SelectValue placeholder="Select tenant" /></SelectTrigger>
          <SelectContent>{tenants.map((t) => <SelectItem key={t.id} value={t.id} data-testid={`sa-feature-tenant-${t.slug}`}>{t.name}</SelectItem>)}</SelectContent>
        </Select>
      </Panel>
      <div className="grid gap-3 sm:grid-cols-2">
        {features.map((f) => (
          <Panel key={f.key} className="flex items-center justify-between" data-testid={`sa-feature-${f.key}`}>
            <div>
              <p className="text-sm font-medium">{f.name}</p>
              <p className="text-[11px] font-mono text-muted-foreground">{f.key}{f.configured ? "" : " · default"}</p>
            </div>
            <Switch checked={f.enabled} data-testid={`sa-feature-toggle-${f.key}`} onCheckedChange={(v) => toggle(f, v)} />
          </Panel>
        ))}
      </div>
    </div>
  );
}

// ----------------------------- Roles & Permissions -----------------------------
export function SARoles() {
  const [roles, setRoles] = useState([]);
  const [perms, setPerms] = useState([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", permission_codes: [] });
  const load = useCallback(async () => {
    const [r, p] = await Promise.all([api.get("/roles"), api.get("/permissions")]);
    setRoles(r.data.filter((x) => x.tenant_id === null)); setPerms(p.data);
  }, []);
  useEffect(() => { load(); }, [load]);
  const create = async () => {
    try { await api.post("/roles", form); toast.success("Role created"); setOpen(false); setForm({ name: "", description: "", permission_codes: [] }); load(); }
    catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
  };
  const toggle = (code) => setForm({ ...form, permission_codes: form.permission_codes.includes(code) ? form.permission_codes.filter((c) => c !== code) : [...form.permission_codes, code] });
  return (
    <div data-testid="sa-roles">
      <PageHeader title="Platform Roles & Permissions" subtitle="Reusable platform-level permission sets you can assign to Platform Admins."
        action={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild><Button data-testid="sa-add-role-btn"><Plus className="h-4 w-4 mr-2" />New Role</Button></DialogTrigger>
            <DialogContent className="max-w-lg">
              <DialogHeader><DialogTitle>Create Platform Role</DialogTitle></DialogHeader>
              <div className="space-y-3">
                <div><Label>Name</Label><Input data-testid="sa-role-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
                <div><Label>Description</Label><Input data-testid="sa-role-desc" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
                <div>
                  <Label>Permissions</Label>
                  <div className="grid grid-cols-2 gap-2 mt-2 max-h-52 overflow-y-auto">
                    {perms.map((p) => (
                      <label key={p.code} className="flex items-center gap-2 text-xs cursor-pointer">
                        <Checkbox data-testid={`sa-roleperm-${p.code}`} checked={form.permission_codes.includes(p.code)} onCheckedChange={() => toggle(p.code)} />
                        <span className="font-mono">{p.code}</span>
                      </label>
                    ))}
                  </div>
                </div>
              </div>
              <DialogFooter><Button data-testid="sa-role-create-submit" onClick={create}>Create Role</Button></DialogFooter>
            </DialogContent>
          </Dialog>
        } />
      <Panel className="p-0 overflow-hidden">
        <Table>
          <TableHeader><TableRow><TableHead>Role</TableHead><TableHead>Description</TableHead><TableHead>Permissions</TableHead></TableRow></TableHeader>
          <TableBody>
            {roles.map((r) => (
              <TableRow key={r.id} data-testid={`sa-role-row-${r.id}`}>
                <TableCell className="font-medium">{r.name}{r.is_system && <Badge className="ml-2 text-[10px]" variant="outline">system</Badge>}</TableCell>
                <TableCell className="text-sm text-muted-foreground">{r.description || "—"}</TableCell>
                <TableCell className="max-w-[360px]"><span className="text-[11px] font-mono text-muted-foreground">{r.permissions.map((p) => p.code).join(", ")}</span></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Panel>
    </div>
  );
}
