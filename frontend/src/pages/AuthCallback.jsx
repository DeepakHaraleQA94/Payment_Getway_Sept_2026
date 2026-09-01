import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

export default function AuthCallback() {
  const navigate = useNavigate();
  const { setUser, checkAuth } = useAuth();
  const processed = useRef(false);

  useEffect(() => {
    if (processed.current) return;
    processed.current = true;

    const hash = window.location.hash || "";
    const match = hash.match(/session_id=([^&]+)/);
    const sessionId = match ? decodeURIComponent(match[1]) : null;

    const run = async () => {
      if (!sessionId) {
        navigate("/login");
        return;
      }
      try {
        const { data } = await api.post("/auth/session", {}, { headers: { "X-Session-ID": sessionId } });
        setUser(data.user);
        window.history.replaceState({}, document.title, "/dashboard");
        await checkAuth();
        navigate("/dashboard");
      } catch {
        navigate("/login");
      }
    };
    run();
  }, [navigate, setUser, checkAuth]);

  return (
    <div className="min-h-screen flex items-center justify-center cp-grid-bg" data-testid="auth-callback">
      <div className="flex flex-col items-center gap-3 text-muted-foreground">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
        <p className="font-mono text-sm">Establishing secure session…</p>
      </div>
    </div>
  );
}
