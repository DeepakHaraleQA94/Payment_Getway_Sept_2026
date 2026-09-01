import { NavLink, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, Building2, CreditCard, Undo2, Plug, Percent,
  BookOpen, Banknote, Users, ToggleLeft, ScrollText, Activity, Zap, LogOut,
} from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";

const NAV = [
  { to: "/dashboard", label: "Overview", icon: LayoutDashboard, testid: "nav-overview" },
  { to: "/dashboard/payments", label: "Payments", icon: CreditCard, testid: "nav-payments" },
  { to: "/dashboard/refunds", label: "Refunds", icon: Undo2, testid: "nav-refunds" },
  { to: "/dashboard/ledger", label: "Balance & Ledger", icon: BookOpen, testid: "nav-ledger" },
  { to: "/dashboard/settlements", label: "Settlements", icon: Banknote, testid: "nav-settlements" },
  { to: "/dashboard/providers", label: "Providers", icon: Plug, testid: "nav-providers" },
  { to: "/dashboard/fees", label: "Fee Engine", icon: Percent, testid: "nav-fees" },
  { to: "/dashboard/tenants", label: "Tenants", icon: Building2, testid: "nav-tenants" },
  { to: "/dashboard/access", label: "Access Control", icon: Users, testid: "nav-access" },
  { to: "/dashboard/features", label: "Feature Flags", icon: ToggleLeft, testid: "nav-features" },
  { to: "/dashboard/audit", label: "Audit Log", icon: ScrollText, testid: "nav-audit" },
  { to: "/dashboard/monitoring", label: "Monitoring", icon: Activity, testid: "nav-monitoring" },
];

export default function Layout({ children }) {
  const { user, logout, tenants, selectedTenantId, setSelectedTenantId } = useAuth();
  const navigate = useNavigate();

  const doLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <div className="min-h-screen flex bg-background">
      <aside className="hidden md:flex flex-col w-64 border-r border-border shrink-0 h-screen sticky top-0">
        <div className="flex items-center gap-2.5 px-5 h-16 border-b border-border">
          <div className="h-8 w-8 rounded-lg bg-primary flex items-center justify-center">
            <Zap className="h-5 w-5 text-white" strokeWidth={2.5} />
          </div>
          <span className="font-heading text-lg font-bold tracking-tight">CloudPay</span>
        </div>
        <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-1">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/dashboard"}
              data-testid={item.testid}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                  isActive
                    ? "bg-primary/15 text-primary font-medium"
                    : "text-muted-foreground hover:text-foreground hover:bg-secondary/60"
                }`
              }
            >
              <item.icon className="h-[18px] w-[18px]" />
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="p-3 border-t border-border">
          <div className="px-2 py-2 mb-1">
            <p className="text-sm font-medium truncate">{user?.name || user?.email}</p>
            <p className="text-xs font-mono text-muted-foreground truncate">
              {user?.role_name || (user?.is_superadmin ? "superadmin" : "member")}
            </p>
          </div>
          <Button variant="ghost" size="sm" data-testid="logout-button" onClick={doLogout} className="w-full justify-start text-muted-foreground">
            <LogOut className="h-4 w-4 mr-2" /> Sign out
          </Button>
        </div>
      </aside>

      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-16 border-b border-border cp-glass sticky top-0 z-20 flex items-center justify-between px-4 sm:px-6">
          <div className="flex items-center gap-3">
            <span className="text-xs font-mono uppercase tracking-wider text-muted-foreground hidden sm:inline">Active Tenant</span>
            <Select value={selectedTenantId || ""} onValueChange={setSelectedTenantId}>
              <SelectTrigger className="w-[200px]" data-testid="tenant-selector">
                <SelectValue placeholder="Select tenant" />
              </SelectTrigger>
              <SelectContent>
                {tenants.map((t) => (
                  <SelectItem key={t.id} value={t.id} data-testid={`tenant-option-${t.slug}`}>
                    {t.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2 text-xs font-mono px-3 py-1.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
            SANDBOX
          </div>
        </header>
        <main className="flex-1 p-4 sm:p-6 lg:p-8 max-w-[1400px] w-full mx-auto">{children}</main>
      </div>
    </div>
  );
}
