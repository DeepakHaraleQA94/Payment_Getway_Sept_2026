import { useEffect, useState, useCallback } from "react";
import { Plus, ShieldCheck } from "lucide-react";
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

export default function AccessControl() {
  const { selectedTenantId } = useAuth();
  const [users, setUsers] = useState([]);
  const [roles, setRoles] = useState([]);
  const [perms, setPerms] = useState([]);
  const [openUser, setOpenUser] = useState(false);
  const [openRole, setOpenRole] = useState(false);
  const [uForm, setUForm] = useState({ email: "", name: "", password: "", role_id: "" });
  const [rForm, setRForm] = useState({ name: "", description: "", permission_codes: [] });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [u, r, p] = await Promise.all([
      api.get("/users"), api.get("/roles"), api.get("/permissions"),
    ]);
    setUsers(u.data); setRoles(r.data); setPerms(p.data);
  }, []);
  useEffect(() => { load(); }, [load]);

  const createUser = async () => {
    setBusy(true);
    try {
      await api.post("/users", {
        email: uForm.email, name: uForm.name, password: uForm.password,
        role_id: uForm.role_id || null,
      }, { params: selectedTenantId ? { tenant_id: selectedTenantId } : {} });
      toast.success("User created");
      setOpenUser(false); setUForm({ email: "", name: "", password: "", role_id: "" }); load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const createRole = async () => {
    setBusy(true);
    try {
      await api.post("/roles", rForm, { params: selectedTenantId ? { tenant_id: selectedTenantId } : {} });
      toast.success("Role created");
      setOpenRole(false); setRForm({ name: "", description: "", permission_codes: [] }); load();
    } catch (e) { toast.error(formatApiError(e.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const togglePerm = (code) => {
    setRForm((f) => ({
      ...f,
      permission_codes: f.permission_codes.includes(code)
        ? f.permission_codes.filter((c) => c !== code)
        : [...f.permission_codes, code],
    }));
  };

  return (
    <div data-testid="access-page">
      <PageHeader title="Access Control" subtitle="Users, roles and the permission matrix." />
      <Tabs defaultValue="users">
        <TabsList data-testid="access-tabs">
          <TabsTrigger value="users" data-testid="tab-users">Users</TabsTrigger>
          <TabsTrigger value="roles" data-testid="tab-roles">Roles & Permissions</TabsTrigger>
        </TabsList>

        <TabsContent value="users" className="mt-4">
          <div className="flex justify-end mb-4">
            <Dialog open={openUser} onOpenChange={setOpenUser}>
              <DialogTrigger asChild><Button data-testid="add-user-button"><Plus className="h-4 w-4 mr-2" /> New User</Button></DialogTrigger>
              <DialogContent data-testid="add-user-dialog">
                <DialogHeader>
                  <DialogTitle>Create User</DialogTitle>
                  <DialogDescription>Add a user and optionally assign a role.</DialogDescription>
                </DialogHeader>
                <div className="space-y-4 py-2">
                  <div className="space-y-2"><Label>Email</Label>
                    <Input data-testid="user-email-input" value={uForm.email} onChange={(e) => setUForm({ ...uForm, email: e.target.value })} /></div>
                  <div className="space-y-2"><Label>Name</Label>
                    <Input data-testid="user-name-input" value={uForm.name} onChange={(e) => setUForm({ ...uForm, name: e.target.value })} /></div>
                  <div className="space-y-2"><Label>Password (min 8)</Label>
                    <Input data-testid="user-password-input" type="password" value={uForm.password} onChange={(e) => setUForm({ ...uForm, password: e.target.value })} /></div>
                  <div className="space-y-2"><Label>Role</Label>
                    <select data-testid="user-role-select" className="w-full h-10 rounded-md bg-background border border-input px-3 text-sm"
                      value={uForm.role_id} onChange={(e) => setUForm({ ...uForm, role_id: e.target.value })}>
                      <option value="">No role</option>
                      {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                    </select>
                  </div>
                </div>
                <DialogFooter><Button data-testid="submit-user-button" onClick={createUser} disabled={busy}>Create</Button></DialogFooter>
              </DialogContent>
            </Dialog>
          </div>
          <Panel className="p-0 overflow-hidden">
            {users.length === 0 ? <EmptyState message="No users." testid="users-empty" /> : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader><TableRow>
                    <TableHead>Email</TableHead><TableHead>Name</TableHead><TableHead>Provider</TableHead><TableHead>Status</TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {users.map((u) => (
                      <TableRow key={u.id} data-testid={`user-row-${u.email}`}>
                        <TableCell className="font-mono text-xs">{u.email}</TableCell>
                        <TableCell>{u.name}</TableCell>
                        <TableCell className="font-mono text-xs">{u.is_superadmin ? "superadmin" : u.auth_provider}</TableCell>
                        <TableCell><StatusBadge status={u.status} /></TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </Panel>
        </TabsContent>

        <TabsContent value="roles" className="mt-4">
          <div className="flex justify-end mb-4">
            <Dialog open={openRole} onOpenChange={setOpenRole}>
              <DialogTrigger asChild><Button data-testid="add-role-button"><Plus className="h-4 w-4 mr-2" /> New Role</Button></DialogTrigger>
              <DialogContent data-testid="add-role-dialog">
                <DialogHeader>
                  <DialogTitle>Create Role</DialogTitle>
                  <DialogDescription>Define a role and select its permissions.</DialogDescription>
                </DialogHeader>
                <div className="space-y-4 py-2">
                  <div className="space-y-2"><Label>Name</Label>
                    <Input data-testid="role-name-input" value={rForm.name} onChange={(e) => setRForm({ ...rForm, name: e.target.value })} /></div>
                  <div className="space-y-2"><Label>Description</Label>
                    <Input data-testid="role-desc-input" value={rForm.description} onChange={(e) => setRForm({ ...rForm, description: e.target.value })} /></div>
                  <div className="space-y-2">
                    <Label>Permissions</Label>
                    <div className="grid grid-cols-1 gap-1.5 max-h-52 overflow-y-auto pr-1">
                      {perms.map((p) => (
                        <label key={p.code} className="flex items-center gap-2 text-sm p-2 rounded hover:bg-secondary/60 cursor-pointer">
                          <input type="checkbox" data-testid={`perm-${p.code}`} checked={rForm.permission_codes.includes(p.code)} onChange={() => togglePerm(p.code)} />
                          <span className="font-mono text-xs">{p.code}</span>
                          <span className="text-muted-foreground text-xs">— {p.description}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                </div>
                <DialogFooter><Button data-testid="submit-role-button" onClick={createRole} disabled={busy || !rForm.name}>Create</Button></DialogFooter>
              </DialogContent>
            </Dialog>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-6">
            {roles.map((r) => (
              <Panel key={r.id} data-testid={`role-card-${r.name}`}>
                <div className="flex items-center gap-3">
                  <div className="h-9 w-9 rounded-lg bg-primary/15 text-primary flex items-center justify-center"><ShieldCheck className="h-4.5 w-4.5" /></div>
                  <div><p className="font-medium">{r.name}</p><p className="text-xs text-muted-foreground">{r.is_system ? "system role" : "tenant role"}</p></div>
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {(r.permissions || []).slice(0, 8).map((p) => (
                    <span key={p.id} className="text-xs font-mono px-2 py-0.5 rounded bg-secondary/60 border border-border">{p.code}</span>
                  ))}
                  {r.permissions?.length > 8 && <span className="text-xs text-muted-foreground">+{r.permissions.length - 8} more</span>}
                  {(!r.permissions || r.permissions.length === 0) && <span className="text-xs text-muted-foreground">No permissions</span>}
                </div>
              </Panel>
            ))}
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
