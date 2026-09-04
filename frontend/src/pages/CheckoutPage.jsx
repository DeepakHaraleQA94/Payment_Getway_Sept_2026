import { useEffect, useState, useCallback } from "react";
import { useParams } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import { Lock, CheckCircle2, Zap, Loader2, Copy, Smartphone, QrCode, ChevronLeft, XCircle, Clock, ShieldCheck } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { toast } from "sonner";
import { api, money, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const APP_STYLES = {
  phonepe: { bg: "#5f259f", initials: "Pe" },
  gpay: { bg: "#1a73e8", initials: "G" },
  paytm: { bg: "#012970", initials: "Pt" },
  bhim: { bg: "#00806a", initials: "B" },
  other: { bg: "#334155", initials: "UPI" },
  qr: { bg: "#0f172a", initials: "QR" },
};

// ---- Demo UPI journey (sandbox demo_upi provider) ----
function DemoUpiCheckout({ token, session, accent }) {
  const [stage, setStage] = useState("apps"); // apps -> qr | pin -> processing -> result
  const [info, setInfo] = useState(null);
  const [app, setApp] = useState(null);
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null); // paid | failed | pending

  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get(`/public/checkout/${token}/upi`);
        setInfo(data);
      } catch (e) { /* fall back to session data */ }
    })();
  }, [token]);

  const authorize = async (outcome) => {
    setBusy(true);
    setStage("processing");
    try {
      const { data } = await api.post(`/public/checkout/${token}/upi/pay`, {
        upi_app: app?.key, outcome, customer_email: session?.customer_email || null,
      });
      if (data.status === "paid") { setResult("paid"); }
      else { setResult(outcome); }
    } catch (e) {
      setResult("failed");
      toast.error(formatApiError(e.response?.data?.detail) || "Payment failed");
    } finally {
      setBusy(false);
      setStage("result");
    }
  };

  const pickApp = (a) => {
    setApp(a);
    if (a.key === "qr") setStage("qr");
    else { setPin(""); setStage("pin"); }
  };

  const reset = () => { setStage("apps"); setApp(null); setPin(""); setResult(null); };

  const amount = money(session?.amount_minor, session?.currency);
  const upiLink = info?.upi_link || `upi://pay?pa=cloudpay@mockbank&am=${(session?.amount_minor || 0) / 100}&cu=INR`;

  return (
    <div data-testid="demo-upi-checkout">
      <div className="flex items-center justify-between mb-1">
        <p className="text-xs font-mono uppercase tracking-wider text-muted-foreground">{session?.merchant}</p>
        <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20">UPI · DEMO</span>
      </div>
      <div className="flex items-end justify-between mt-1 mb-1">
        <h1 className="font-heading text-3xl font-bold" data-testid="checkout-amount">{amount}</h1>
        <span className="text-xs font-mono text-muted-foreground">{session?.reference}</span>
      </div>
      <p className="text-sm text-muted-foreground mb-5">{session?.description || "Complete your payment via UPI."}</p>

      <AnimatePresence mode="wait">
        {/* STAGE: app choice */}
        {stage === "apps" && (
          <motion.div key="apps" initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -8 }}
            className="space-y-3" data-testid="upi-app-stage">
            <p className="text-sm font-medium flex items-center gap-1.5"><Smartphone className="h-4 w-4 text-primary" /> Choose how to pay</p>
            <div className="grid grid-cols-3 gap-3">
              {(info?.apps || []).map((a) => {
                const st = APP_STYLES[a.key] || APP_STYLES.other;
                const isQr = a.key === "qr";
                return (
                  <button key={a.key} type="button" data-testid={`upi-app-${a.key}`} onClick={() => pickApp(a)}
                    className="flex flex-col items-center gap-2 p-3 rounded-xl border border-border hover:border-primary/50 transition-colors bg-secondary/20">
                    <span className="h-11 w-11 rounded-full flex items-center justify-center text-white font-bold text-sm" style={{ background: st.bg }}>
                      {isQr ? <QrCode className="h-5 w-5" /> : st.initials}
                    </span>
                    <span className="text-xs text-center leading-tight">{a.label}</span>
                  </button>
                );
              })}
            </div>
            <p className="text-xs text-center text-muted-foreground pt-1">Sandbox demo — no real UPI app opens and no funds move.</p>
          </motion.div>
        )}

        {/* STAGE: QR */}
        {stage === "qr" && (
          <motion.div key="qr" initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -8 }}
            className="space-y-4" data-testid="upi-qr-stage">
            <button onClick={reset} className="text-xs text-muted-foreground inline-flex items-center hover:text-foreground" data-testid="upi-qr-back">
              <ChevronLeft className="h-3.5 w-3.5" /> Back
            </button>
            <p className="text-sm font-medium text-center">Scan with any UPI app</p>
            <div className="flex justify-center">
              <div className="p-4 bg-white rounded-xl" data-testid="upi-qr-image">
                <QRCodeSVG value={upiLink} size={188} level="M" />
              </div>
            </div>
            <div className="rounded-lg border border-border bg-secondary/40 p-3 flex items-center justify-between gap-2">
              <div className="min-w-0">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Pay to</p>
                <p className="font-mono text-sm truncate" data-testid="upi-qr-vpa">{info?.vpa || "cloudpay@mockbank"}</p>
              </div>
              <Button variant="outline" size="sm" className="shrink-0" data-testid="upi-qr-copy"
                onClick={() => { navigator.clipboard?.writeText(info?.vpa || ""); toast.success("UPI ID copied"); }}>
                <Copy className="h-3.5 w-3.5 mr-1" /> Copy
              </Button>
            </div>
            <Button className="w-full text-white" style={{ background: accent }} data-testid="upi-qr-paid" onClick={() => authorize("success")} disabled={busy}>
              I've completed the payment
            </Button>
            <button onClick={() => authorize("failed")} className="w-full text-xs text-muted-foreground hover:text-foreground" data-testid="upi-qr-simulate-fail">
              Simulate a failed scan
            </button>
          </motion.div>
        )}

        {/* STAGE: PIN */}
        {stage === "pin" && (
          <motion.div key="pin" initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -8 }}
            className="space-y-4" data-testid="upi-pin-stage">
            <button onClick={reset} className="text-xs text-muted-foreground inline-flex items-center hover:text-foreground" data-testid="upi-pin-back">
              <ChevronLeft className="h-3.5 w-3.5" /> Back
            </button>
            <div className="flex items-center gap-2.5">
              <span className="h-9 w-9 rounded-full flex items-center justify-center text-white font-bold text-xs" style={{ background: (APP_STYLES[app?.key] || APP_STYLES.other).bg }}>
                {(APP_STYLES[app?.key] || APP_STYLES.other).initials}
              </span>
              <div>
                <p className="text-sm font-medium">{app?.label}</p>
                <p className="text-xs text-muted-foreground">Paying {amount} to {info?.payee || session?.merchant}</p>
              </div>
            </div>
            <div className="rounded-lg border border-border bg-secondary/30 p-4 space-y-3">
              <p className="text-sm font-medium flex items-center gap-1.5"><ShieldCheck className="h-4 w-4 text-primary" /> Enter UPI PIN</p>
              <Input data-testid="upi-pin-input" inputMode="numeric" maxLength={6} value={pin} type="password"
                onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 6))}
                className="font-mono tracking-[0.5em] text-center text-lg" placeholder="••••" />
              <div className="grid grid-cols-3 gap-2">
                {[1,2,3,4,5,6,7,8,9,0].map((n) => (
                  <button key={n} type="button" data-testid={`upi-pin-key-${n}`} onClick={() => setPin((p) => (p.length < 6 ? p + n : p))}
                    className={`py-2.5 rounded-md border border-border bg-card hover:bg-secondary/60 text-sm font-mono ${n === 0 ? "col-start-2" : ""}`}>{n}</button>
                ))}
              </div>
            </div>
            <Button className="w-full text-white" style={{ background: accent }} data-testid="upi-pin-authorize" onClick={() => authorize("success")} disabled={busy || pin.length < 4}>
              <Lock className="h-4 w-4 mr-2" /> Authorize {amount}
            </Button>
            <div className="flex items-center gap-2">
              <button onClick={() => authorize("failed")} className="flex-1 text-xs text-muted-foreground hover:text-foreground py-1" data-testid="upi-pin-simulate-fail">Simulate failure</button>
              <button onClick={() => authorize("pending")} className="flex-1 text-xs text-muted-foreground hover:text-foreground py-1" data-testid="upi-pin-simulate-pending">Simulate pending</button>
            </div>
          </motion.div>
        )}

        {/* STAGE: processing */}
        {stage === "processing" && (
          <motion.div key="proc" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="py-12 text-center" data-testid="upi-processing-stage">
            <Loader2 className="h-10 w-10 animate-spin text-primary mx-auto mb-4" />
            <p className="text-sm text-muted-foreground">Authorizing with your bank…</p>
          </motion.div>
        )}

        {/* STAGE: result */}
        {stage === "result" && (
          <motion.div key="res" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
            className="py-8 text-center" data-testid="upi-result-stage">
            {result === "paid" ? (
              <div data-testid="upi-result-paid">
                <CheckCircle2 className="h-14 w-14 text-emerald-400 mx-auto mb-4" />
                <h2 className="font-heading text-xl font-semibold">Payment successful</h2>
                <p className="text-sm text-muted-foreground mt-2">Your sandbox UPI payment of {amount} is complete.</p>
              </div>
            ) : result === "pending" ? (
              <div data-testid="upi-result-pending">
                <Clock className="h-14 w-14 text-amber-400 mx-auto mb-4" />
                <h2 className="font-heading text-xl font-semibold">Payment pending</h2>
                <p className="text-sm text-muted-foreground mt-2">Simulated — the bank has not confirmed yet. No payment was recorded.</p>
                <Button variant="outline" className="mt-5" onClick={reset} data-testid="upi-result-retry">Try again</Button>
              </div>
            ) : (
              <div data-testid="upi-result-failed">
                <XCircle className="h-14 w-14 text-red-400 mx-auto mb-4" />
                <h2 className="font-heading text-xl font-semibold">Payment failed</h2>
                <p className="text-sm text-muted-foreground mt-2">Simulated — no payment was recorded. You can try another method.</p>
                <Button variant="outline" className="mt-5" onClick={reset} data-testid="upi-result-retry">Try again</Button>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export default function CheckoutPage() {
  const { token } = useParams();
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState("");
  const [card, setCard] = useState("4242 4242 4242 4242");
  const [paying, setPaying] = useState(false);
  const [paid, setPaid] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get(`/public/checkout/${token}`);
      setSession(data);
      if (data.customer_email) setEmail(data.customer_email);
      if (data.status === "paid") setPaid(true);
    } catch (e) {
      setError(formatApiError(e.response?.data?.detail) || "Checkout not found");
    } finally { setLoading(false); }
  }, [token]);
  useEffect(() => { load(); }, [load]);

  const pay = async () => {
    setPaying(true);
    setError(null);
    try {
      const { data } = await api.post(`/public/checkout/${token}/pay`, { customer_email: email, card_number: card });
      setPaid(true);
      if (data.success_url) setTimeout(() => (window.location.href = data.success_url), 1200);
    } catch (e) {
      setError(formatApiError(e.response?.data?.detail) || "Payment failed");
    } finally { setPaying(false); }
  };

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center cp-grid-bg"><Loader2 className="h-8 w-8 animate-spin text-primary" /></div>;
  }

  const accent = session?.brand_accent || "#3B82F6";
  const isDemoUpi = session?.provider_key === "demo_upi";

  return (
    <div className="min-h-screen flex items-center justify-center p-6 cp-grid-bg" data-testid="public-checkout-page"
      style={{ "--brand": accent }}>
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}
        className="w-full max-w-md rounded-lg border border-border bg-card p-6 sm:p-8">
        <div className="flex items-center gap-2 mb-6">
          {session?.logo_url ? (
            <img src={`${process.env.REACT_APP_BACKEND_URL}${session.logo_url}`} alt="logo" data-testid="checkout-logo"
              className="h-8 w-8 rounded-lg object-contain bg-white/5" />
          ) : (
            <div className="h-8 w-8 rounded-lg flex items-center justify-center" style={{ background: accent }}>
              <Zap className="h-5 w-5 text-white" strokeWidth={2.5} />
            </div>
          )}
          <span className="font-heading text-lg font-bold">{session?.merchant || "CloudPay"}</span>
          <span className="ml-auto text-xs font-mono px-2 py-1 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20">SANDBOX</span>
        </div>

        {error && !session ? (
          <p className="text-sm text-red-400" data-testid="checkout-error">{error}</p>
        ) : paid ? (
          <div className="text-center py-8" data-testid="checkout-success">
            <CheckCircle2 className="h-14 w-14 text-emerald-400 mx-auto mb-4" />
            <h2 className="font-heading text-xl font-semibold">Payment successful</h2>
            <p className="text-sm text-muted-foreground mt-2">Thank you. Your sandbox payment to {session?.merchant} is complete.</p>
          </div>
        ) : session?.status === "expired" ? (
          <p className="text-sm text-amber-400" data-testid="checkout-expired">This checkout link has expired.</p>
        ) : isDemoUpi ? (
          <DemoUpiCheckout token={token} session={session} accent={accent} />
        ) : (
          <>
            <p className="text-xs font-mono uppercase tracking-wider text-muted-foreground">{session?.merchant}</p>
            <div className="flex items-end justify-between mt-2 mb-1">
              <h1 className="font-heading text-3xl font-bold" data-testid="checkout-amount">{money(session?.amount_minor, session?.currency)}</h1>
              <span className="text-xs font-mono text-muted-foreground">{session?.reference}</span>
            </div>
            <p className="text-sm text-muted-foreground mb-6">{session?.description || "Complete your payment below."}</p>

            {session?.acceptance?.upi_vpa && (
              <div className="mb-6 rounded-lg border border-border bg-secondary/40 p-4" data-testid="checkout-upi-block">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">Pay to this UPI ID</p>
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="font-mono text-sm truncate" data-testid="checkout-upi-vpa">{session.acceptance.upi_vpa}</p>
                    <p className="text-xs text-muted-foreground">{session.acceptance.display_name}{session.acceptance.bank_name ? ` · ${session.acceptance.bank_name}` : ""}</p>
                  </div>
                  <Button variant="outline" size="sm" className="shrink-0" data-testid="checkout-upi-copy"
                    onClick={() => { navigator.clipboard?.writeText(session.acceptance.upi_vpa); toast.success("UPI ID copied"); }}>
                    <Copy className="h-3.5 w-3.5 mr-1" /> Copy
                  </Button>
                </div>
              </div>
            )}

            <div className="space-y-4">
              <div className="space-y-2"><Label>Email</Label>
                <Input data-testid="checkout-pay-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" /></div>
              <div className="space-y-2"><Label>Card number (sandbox)</Label>
                <Input data-testid="checkout-pay-card" value={card} onChange={(e) => setCard(e.target.value)} className="font-mono" /></div>
              {error && <p className="text-sm text-red-400" data-testid="checkout-pay-error">{error}</p>}
              <Button className="w-full text-white" style={{ background: accent }} data-testid="checkout-pay-button" onClick={pay} disabled={paying}>
                <Lock className="h-4 w-4 mr-2" /> {paying ? "Processing…" : `Pay ${money(session?.amount_minor, session?.currency)}`}
              </Button>
              <p className="text-xs text-center text-muted-foreground">No real funds move. Powered by CloudPay sandbox.</p>
            </div>
          </>
        )}
      </motion.div>
    </div>
  );
}
