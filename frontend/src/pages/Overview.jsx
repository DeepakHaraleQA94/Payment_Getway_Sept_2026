import { useEffect, useState, useCallback } from "react";
import {
  Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid,
} from "recharts";
import { TrendingUp, CheckCircle2, XCircle, Layers, ArrowUpRight } from "lucide-react";
import { api, money } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Panel, StatusBadge, EmptyState } from "@/components/common";

function Kpi({ icon: Icon, label, value, sub, accent, testid }) {
  return (
    <Panel className="cp-anim">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-mono uppercase tracking-wider text-muted-foreground">{label}</p>
          <p className="font-mono text-2xl sm:text-3xl font-semibold mt-2 tracking-tight" data-testid={testid}>{value}</p>
          {sub && <p className="text-xs text-muted-foreground mt-1">{sub}</p>}
        </div>
        <div className={`h-10 w-10 rounded-lg flex items-center justify-center ${accent}`}>
          <Icon className="h-5 w-5" />
        </div>
      </div>
    </Panel>
  );
}

export default function Overview() {
  const { selectedTenantId, tenants } = useAuth();
  const [summary, setSummary] = useState(null);
  const [statusData, setStatusData] = useState([]);
  const [payments, setPayments] = useState([]);
  const tenant = tenants.find((t) => t.id === selectedTenantId);
  const currency = tenant?.default_currency || "USD";

  const load = useCallback(async () => {
    if (!selectedTenantId) return;
    const p = { params: { tenant_id: selectedTenantId } };
    const [s, r, pay] = await Promise.all([
      api.get("/dashboard/summary", p),
      api.get("/reports/payments-by-status", p),
      api.get("/payments", p),
    ]);
    setSummary(s.data);
    setStatusData(r.data);
    setPayments(pay.data.slice(0, 6));
  }, [selectedTenantId]);

  useEffect(() => { load(); }, [load]);

  const chart = statusData.map((d) => ({ name: d.status, amount: d.amount_minor / 100, count: d.count }));

  return (
    <div data-testid="overview-page">
      <PageHeader title="Overview" subtitle={`Live orchestration metrics for ${tenant?.name || "tenant"}`} />
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 md:gap-6">
        <Kpi icon={TrendingUp} label="Gross Turnover" value={money(summary?.turnover?.gross_minor, currency)}
             sub={`${summary?.turnover?.txn_count || 0} settled txns`} accent="bg-primary/15 text-primary" testid="kpi-turnover" />
        <Kpi icon={ArrowUpRight} label="Net Volume" value={money(summary?.turnover?.net_minor, currency)}
             sub={`${money(summary?.turnover?.fees_minor, currency)} fees`} accent="bg-indigo-500/15 text-indigo-400" testid="kpi-net" />
        <Kpi icon={CheckCircle2} label="Success Rate" value={`${summary?.success_rate ?? 0}%`}
             sub={`${summary?.payments_succeeded || 0}/${summary?.payments_total || 0} succeeded`} accent="bg-emerald-500/15 text-emerald-400" testid="kpi-success" />
        <Kpi icon={Layers} label="Tenants" value={summary?.tenant_count ?? 0}
             sub="on platform" accent="bg-sky-500/15 text-sky-400" testid="kpi-tenants" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mt-6">
        <Panel className="lg:col-span-2">
          <h3 className="font-heading text-lg font-medium mb-4">Volume by Status</h3>
          {chart.length === 0 ? (
            <EmptyState message="No payment data yet. Create a payment to see analytics." testid="overview-chart-empty" />
          ) : (
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={chart} margin={{ left: -12, right: 8, top: 8 }}>
                <defs>
                  <linearGradient id="cpArea" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="hsl(217 91% 60%)" stopOpacity={0.5} />
                    <stop offset="100%" stopColor="hsl(217 91% 60%)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(217 33% 17%)" vertical={false} />
                <XAxis dataKey="name" stroke="hsl(215 20% 65%)" fontSize={11} tickLine={false} axisLine={false} />
                <YAxis stroke="hsl(215 20% 65%)" fontSize={11} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={{ background: "hsl(221 39% 11%)", border: "1px solid hsl(217 33% 17%)", borderRadius: 8, fontSize: 12 }} />
                <Area type="monotone" dataKey="amount" stroke="hsl(217 91% 60%)" strokeWidth={2} fill="url(#cpArea)" />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Panel>

        <Panel>
          <h3 className="font-heading text-lg font-medium mb-4">Recent Payments</h3>
          <div className="space-y-3">
            {payments.length === 0 && <EmptyState message="No payments yet." testid="overview-recent-empty" />}
            {payments.map((p) => (
              <div key={p.id} className="flex items-center justify-between py-2 border-b border-border last:border-0">
                <div className="min-w-0">
                  <p className="text-sm font-mono truncate">{p.reference}</p>
                  <p className="text-xs text-muted-foreground truncate">{p.customer_email || "—"}</p>
                </div>
                <div className="text-right shrink-0 ml-3">
                  <p className="text-sm font-mono">{money(p.amount_minor, p.currency)}</p>
                  <StatusBadge status={p.status} />
                </div>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}
