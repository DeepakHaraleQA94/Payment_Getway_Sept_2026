import { useEffect, useState, useCallback, useMemo } from "react";
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
  const [allPayments, setAllPayments] = useState([]);
  const [succeededOnly, setSucceededOnly] = useState(false);
  const [rangeDays, setRangeDays] = useState(0); // 0 = all time
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
    setAllPayments(pay.data || []);
  }, [selectedTenantId]);

  const railByCurrency = useMemo(() => {
    const byCcy = {};
    const cutoff = rangeDays ? Date.now() - rangeDays * 86400000 : 0;
    allPayments
      .filter((x) => !succeededOnly || ["succeeded", "captured"].includes(x.status))
      .filter((x) => !cutoff || new Date(x.created_at).getTime() >= cutoff)
      .forEach((x) => {
        const ccy = (x.currency || "USD").toUpperCase();
        const m = String((x.metadata && x.metadata.method) || (x.provider_key === "demo_upi" ? "upi" : "card")).toLowerCase();
        const rail = m.includes("upi") ? "upi" : "card";
        if (!byCcy[ccy]) byCcy[ccy] = { upi: { count: 0, amount: 0 }, card: { count: 0, amount: 0 } };
        byCcy[ccy][rail].count += 1;
        byCcy[ccy][rail].amount += x.amount_minor || 0;
      });
    return byCcy;
  }, [allPayments, succeededOnly, rangeDays]);

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

      <Panel className="mt-6" data-testid="rail-mix-card">
        {(() => {
          const currencies = Object.keys(railByCurrency).sort();
          const grandTotal = currencies.reduce((n, c) => n + railByCurrency[c].upi.count + railByCurrency[c].card.count, 0);
          return (
            <div>
              <div className="flex items-center justify-between mb-4">
                <h3 className="font-heading text-lg font-medium">Payment Mix by Rail</h3>
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-1" data-testid="rail-mix-range">
                    {[[0, "All"], [7, "7d"], [30, "30d"]].map(([d, label]) => (
                      <button key={d} type="button" data-testid={`rail-mix-range-${d}`}
                        onClick={() => setRangeDays(d)}
                        className={`text-xs font-mono px-2 py-1 rounded-md border transition-colors ${
                          rangeDays === d
                            ? "bg-primary/20 border-primary/60 text-primary"
                            : "bg-secondary/40 border-border text-muted-foreground hover:border-primary/40"
                        }`}>
                        {label}
                      </button>
                    ))}
                  </div>
                  <button
                    type="button"
                    data-testid="rail-mix-succeeded-toggle"
                    onClick={() => setSucceededOnly((v) => !v)}
                    className={`text-xs font-mono px-2.5 py-1 rounded-md border transition-colors ${
                      succeededOnly
                        ? "bg-primary/20 border-primary/60 text-primary"
                        : "bg-secondary/40 border-border text-muted-foreground hover:border-primary/40"
                    }`}>
                    Succeeded only
                  </button>
                  <span className="text-xs font-mono text-muted-foreground">{grandTotal} payments</span>
                </div>
              </div>
              {currencies.length === 0 ? (
                <EmptyState message={(succeededOnly || rangeDays) ? "No payments match the current filters." : "No payments yet to break down by rail."} testid="rail-mix-empty" />
              ) : (
                <div className="space-y-5">
                  {currencies.map((ccy) => {
                    const mix = railByCurrency[ccy];
                    const upiC = mix.upi.count, cardC = mix.card.count;
                    const total = upiC + cardC;
                    const upiPct = total ? Math.round((upiC / total) * 100) : 0;
                    const cardPct = total ? 100 - upiPct : 0;
                    return (
                      <div key={ccy} data-testid={`rail-mix-ccy-${ccy}`}>
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-sm font-mono font-medium">{ccy}</span>
                          <span className="text-xs font-mono text-muted-foreground">{total} payments</span>
                        </div>
                        <div className="flex h-3 w-full overflow-hidden rounded-full bg-secondary/40" data-testid={`rail-mix-bar-${ccy}`}>
                          <div className="bg-primary" style={{ width: `${upiPct}%` }} title={`UPI ${upiPct}%`} />
                          <div className="bg-indigo-400" style={{ width: `${cardPct}%` }} title={`Card ${cardPct}%`} />
                        </div>
                        <div className="mt-3 grid grid-cols-2 gap-4">
                          <div data-testid={`rail-mix-upi-${ccy}`}>
                            <div className="flex items-center gap-2">
                              <span className="h-2.5 w-2.5 rounded-sm bg-primary" />
                              <span className="text-sm font-medium">UPI</span>
                              <span className="text-xs font-mono text-muted-foreground">{upiPct}%</span>
                            </div>
                            <p className="mt-1 font-mono text-lg">{money(mix.upi.amount, ccy)}</p>
                            <p className="text-xs text-muted-foreground">{upiC} payments</p>
                          </div>
                          <div data-testid={`rail-mix-card-${ccy}`}>
                            <div className="flex items-center gap-2">
                              <span className="h-2.5 w-2.5 rounded-sm bg-indigo-400" />
                              <span className="text-sm font-medium">Card</span>
                              <span className="text-xs font-mono text-muted-foreground">{cardPct}%</span>
                            </div>
                            <p className="mt-1 font-mono text-lg">{money(mix.card.amount, ccy)}</p>
                            <p className="text-xs text-muted-foreground">{cardC} payments</p>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })()}
      </Panel>

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
