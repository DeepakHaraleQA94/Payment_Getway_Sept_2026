import { useState } from "react";
import { Link, useSearchParams, useNavigate } from "react-router-dom";
import { Zap } from "lucide-react";
import { toast } from "sonner";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/auth/forgot-password", { email });
      setSent(true);
    } catch (err) { toast.error(formatApiError(err.response?.data?.detail)); }
    finally { setBusy(false); }
  };
  return (
    <Shell title="Reset your password" testid="forgot-password-page">
      {sent ? (
        <p className="text-sm text-muted-foreground" data-testid="forgot-sent">
          If an account exists for {email}, a reset link has been generated. Check your notifications/logs
          (email delivery is inactive in this environment).
        </p>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label>Email</Label>
            <Input data-testid="forgot-email-input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <Button type="submit" data-testid="forgot-submit-button" disabled={busy} className="w-full">Send reset link</Button>
        </form>
      )}
      <p className="text-sm text-center text-muted-foreground mt-6"><Link to="/login" className="text-primary hover:underline">Back to sign in</Link></p>
    </Shell>
  );
}

export function ResetPassword() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const token = params.get("token") || "";
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/auth/reset-password", { token, new_password: password });
      toast.success("Password reset. Please sign in.");
      navigate("/login");
    } catch (err) { toast.error(formatApiError(err.response?.data?.detail)); }
    finally { setBusy(false); }
  };
  return (
    <Shell title="Choose a new password" testid="reset-password-page">
      {!token ? (
        <p className="text-sm text-red-400" data-testid="reset-missing-token">Missing or invalid reset token.</p>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label>New password</Label>
            <Input data-testid="reset-password-input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
          </div>
          <Button type="submit" data-testid="reset-submit-button" disabled={busy} className="w-full">Reset password</Button>
        </form>
      )}
    </Shell>
  );
}

function Shell({ title, children, testid }) {
  return (
    <div className="min-h-screen flex items-center justify-center p-6 cp-grid-bg" data-testid={testid}>
      <div className="w-full max-w-md rounded-lg border border-border bg-card p-6 sm:p-8">
        <div className="flex items-center gap-2 mb-6">
          <div className="h-8 w-8 rounded-lg bg-primary flex items-center justify-center"><Zap className="h-5 w-5 text-white" strokeWidth={2.5} /></div>
          <span className="font-heading text-lg font-bold">CloudPay</span>
        </div>
        <h1 className="font-heading text-2xl font-semibold mb-4">{title}</h1>
        {children}
      </div>
    </div>
  );
}
