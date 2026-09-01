import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { motion } from "framer-motion";
import { ShieldCheck, Zap, Lock, ArrowRight } from "lucide-react";
import { toast } from "sonner";
import { api, formatApiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function Login() {
  const navigate = useNavigate();
  const { setUser, checkAuth } = useAuth();
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(false);
  const [mfaToken, setMfaToken] = useState(null);
  const [mfaCode, setMfaCode] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const path = mode === "login" ? "/auth/login" : "/auth/register";
      const payload = mode === "login" ? { email, password } : { email, password, name };
      const { data } = await api.post(path, payload);
      if (data.mfa_required) {
        setMfaToken(data.mfa_token);
        toast.info("Enter your authenticator code");
        return;
      }
      setUser(data);
      await checkAuth();
      toast.success(`Welcome to CloudPay`);
      navigate(data.is_superadmin ? "/superadmin" : "/dashboard");
    } catch (err) {
      toast.error(formatApiError(err.response?.data?.detail) || err.message);
    } finally {
      setLoading(false);
    }
  };

  const verifyMfa = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const { data } = await api.post("/auth/mfa/verify", { mfa_token: mfaToken, code: mfaCode });
      setUser(data);
      await checkAuth();
      toast.success("Signed in");
      navigate(data.is_superadmin ? "/superadmin" : "/dashboard");
    } catch (err) {
      toast.error(formatApiError(err.response?.data?.detail) || err.message);
    } finally {
      setLoading(false);
    }
  };

  const googleLogin = () => {
    // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
    const redirectUrl = window.location.origin + "/dashboard";
    window.location.href = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;
  };

  return (
    <div className="min-h-screen w-full flex cp-grid-bg" data-testid="login-page">
      <div className="hidden lg:flex flex-col justify-between w-[46%] p-12 border-r border-border relative overflow-hidden">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-lg bg-primary flex items-center justify-center">
            <Zap className="h-6 w-6 text-white" strokeWidth={2.5} />
          </div>
          <span className="font-heading text-2xl font-bold tracking-tight">CloudPay</span>
        </div>
        <div className="space-y-6">
          <p className="text-xs font-mono uppercase tracking-[0.25em] text-primary">Payment Orchestration</p>
          <h1 className="font-heading text-4xl xl:text-5xl font-bold leading-[1.1] tracking-tight">
            Multi-tenant payment gateway, engineered for scale.
          </h1>
          <p className="text-muted-foreground max-w-md leading-relaxed">
            Provider-agnostic orchestration with a fee engine, ledger, settlement and full audit trail.
            Sandbox by default — never a fake real-money success.
          </p>
          <div className="flex flex-wrap gap-3 pt-2">
            {[
              { icon: ShieldCheck, label: "Tenant Isolation" },
              { icon: Lock, label: "Server-side Validation" },
              { icon: Zap, label: "Idempotent Mutations" },
            ].map((f) => (
              <div key={f.label} className="flex items-center gap-2 text-sm px-3 py-2 rounded-lg bg-secondary/60 border border-border">
                <f.icon className="h-4 w-4 text-primary" />
                {f.label}
              </div>
            ))}
          </div>
        </div>
        <p className="text-xs font-mono text-muted-foreground">SANDBOX ENVIRONMENT · NO REAL FUNDS MOVE</p>
      </div>

      <div className="flex-1 flex items-center justify-center p-6">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="w-full max-w-md"
        >
          <div className="lg:hidden flex items-center gap-3 mb-8">
            <div className="h-10 w-10 rounded-lg bg-primary flex items-center justify-center">
              <Zap className="h-6 w-6 text-white" strokeWidth={2.5} />
            </div>
            <span className="font-heading text-2xl font-bold">CloudPay</span>
          </div>
          <h2 className="font-heading text-2xl font-semibold">
            {mode === "login" ? "Sign in to console" : "Create your account"}
          </h2>
          <p className="text-sm text-muted-foreground mt-1 mb-6">
            {mode === "login" ? "Access the CloudPay control center." : "Start orchestrating payments."}
          </p>

          <form onSubmit={submit} className="space-y-4" data-testid="auth-form">
            {mfaToken && (
              <div className="space-y-2" data-testid="mfa-challenge">
                <Label htmlFor="mfa">Authenticator code</Label>
                <Input id="mfa" data-testid="mfa-code-input" value={mfaCode} onChange={(e) => setMfaCode(e.target.value)} placeholder="123456" inputMode="numeric" />
                <Button type="button" data-testid="mfa-verify-button" onClick={verifyMfa} disabled={loading} className="w-full font-medium">
                  Verify & sign in
                </Button>
                <p className="text-xs text-muted-foreground">Enter the 6-digit code from your authenticator app.</p>
              </div>
            )}
            {!mfaToken && mode === "register" && (
              <div className="space-y-2">
                <Label htmlFor="name">Full name</Label>
                <Input id="name" data-testid="name-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Jane Doe" />
              </div>
            )}
            {!mfaToken && (
            <>
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input id="email" data-testid="email-input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label htmlFor="password">Password</Label>
                {mode === "login" && (
                  <Link to="/forgot-password" data-testid="forgot-password-link" className="text-xs text-primary hover:underline">Forgot password?</Link>
                )}
              </div>
              <Input id="password" data-testid="password-input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </div>
            <Button type="submit" data-testid="auth-submit-button" disabled={loading} className="w-full font-medium">
              {loading ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
              <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
            </>
            )}
          </form>

          <div className="flex items-center gap-3 my-5">
            <div className="h-px bg-border flex-1" />
            <span className="text-xs font-mono uppercase tracking-wider text-muted-foreground">or</span>
            <div className="h-px bg-border flex-1" />
          </div>

          <Button variant="outline" data-testid="google-login-button" onClick={googleLogin} className="w-full">
            <img src="https://www.google.com/favicon.ico" alt="" className="h-4 w-4 mr-2" />
            Continue with Google
          </Button>

          <p className="text-sm text-center text-muted-foreground mt-6">
            {mode === "login" ? "New to CloudPay?" : "Already have an account?"}{" "}
            <button
              type="button"
              data-testid="toggle-auth-mode"
              onClick={() => setMode(mode === "login" ? "register" : "login")}
              className="text-primary hover:underline font-medium"
            >
              {mode === "login" ? "Create an account" : "Sign in"}
            </button>
          </p>
        </motion.div>
      </div>
    </div>
  );
}
