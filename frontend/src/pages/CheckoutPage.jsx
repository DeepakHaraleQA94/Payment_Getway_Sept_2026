import { useEffect, useState, useCallback } from "react";
import { useParams } from "react-router-dom";
import { motion } from "framer-motion";
import { Lock, CheckCircle2, Zap, Loader2 } from "lucide-react";
import { api, money, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

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

  return (
    <div className="min-h-screen flex items-center justify-center p-6 cp-grid-bg" data-testid="public-checkout-page">
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}
        className="w-full max-w-md rounded-lg border border-border bg-card p-6 sm:p-8">
        <div className="flex items-center gap-2 mb-6">
          <div className="h-8 w-8 rounded-lg bg-primary flex items-center justify-center"><Zap className="h-5 w-5 text-white" strokeWidth={2.5} /></div>
          <span className="font-heading text-lg font-bold">CloudPay</span>
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
        ) : (
          <>
            <p className="text-xs font-mono uppercase tracking-wider text-muted-foreground">{session?.merchant}</p>
            <div className="flex items-end justify-between mt-2 mb-1">
              <h1 className="font-heading text-3xl font-bold" data-testid="checkout-amount">{money(session?.amount_minor, session?.currency)}</h1>
              <span className="text-xs font-mono text-muted-foreground">{session?.reference}</span>
            </div>
            <p className="text-sm text-muted-foreground mb-6">{session?.description || "Complete your payment below."}</p>

            {session?.status === "expired" ? (
              <p className="text-sm text-amber-400" data-testid="checkout-expired">This checkout link has expired.</p>
            ) : (
              <div className="space-y-4">
                <div className="space-y-2"><Label>Email</Label>
                  <Input data-testid="checkout-pay-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" /></div>
                <div className="space-y-2"><Label>Card number (sandbox)</Label>
                  <Input data-testid="checkout-pay-card" value={card} onChange={(e) => setCard(e.target.value)} className="font-mono" /></div>
                {error && <p className="text-sm text-red-400" data-testid="checkout-pay-error">{error}</p>}
                <Button className="w-full" data-testid="checkout-pay-button" onClick={pay} disabled={paying}>
                  <Lock className="h-4 w-4 mr-2" /> {paying ? "Processing…" : `Pay ${money(session?.amount_minor, session?.currency)}`}
                </Button>
                <p className="text-xs text-center text-muted-foreground">No real funds move. Powered by CloudPay sandbox.</p>
              </div>
            )}
          </>
        )}
      </motion.div>
    </div>
  );
}
