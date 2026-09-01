export function PageHeader({ title, subtitle, action, testid }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4 mb-6" data-testid={testid}>
      <div>
        <h1 className="font-heading text-2xl sm:text-3xl font-bold tracking-tight">{title}</h1>
        {subtitle && <p className="text-sm text-muted-foreground mt-1">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function Panel({ children, className = "", ...rest }) {
  return (
    <div className={`rounded-lg border border-border bg-card p-5 sm:p-6 ${className}`} {...rest}>{children}</div>
  );
}

const STATUS_STYLES = {
  succeeded: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  captured: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  active: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  settled: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  verified: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  pending: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  processing: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  partially_refunded: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  created: "bg-sky-500/10 text-sky-400 border-sky-500/20",
  refunded: "bg-indigo-500/10 text-indigo-400 border-indigo-500/20",
  failed: "bg-red-500/10 text-red-400 border-red-500/20",
  cancelled: "bg-red-500/10 text-red-400 border-red-500/20",
  suspended: "bg-red-500/10 text-red-400 border-red-500/20",
};

export function StatusBadge({ status }) {
  const style = STATUS_STYLES[status] || "bg-slate-500/10 text-slate-300 border-slate-500/20";
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border font-mono ${style}`}>
      {String(status || "").replace(/_/g, " ")}
    </span>
  );
}

export function EmptyState({ message, testid }) {
  return (
    <div className="text-center py-16 text-muted-foreground" data-testid={testid}>
      <p className="text-sm">{message}</p>
    </div>
  );
}
