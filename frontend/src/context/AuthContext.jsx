import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null); // null=checking, false=anon, object=authed
  const [tenants, setTenants] = useState([]);
  const [selectedTenantId, setSelectedTenantId] = useState(null);

  const loadTenants = useCallback(async () => {
    try {
      const { data } = await api.get("/tenants");
      setTenants(data);
      const nonPlatform = data.find((t) => !t.is_platform) || data[0];
      setSelectedTenantId((prev) => prev || (nonPlatform ? nonPlatform.id : null));
    } catch {
      /* ignore */
    }
  }, []);

  const checkAuth = useCallback(async () => {
    try {
      const { data } = await api.get("/auth/me");
      setUser(data);
      await loadTenants();
    } catch {
      setUser(false);
    }
  }, [loadTenants]);

  useEffect(() => {
    // If returning from Google OAuth callback, let AuthCallback handle it.
    if (window.location.hash?.includes("session_id=")) {
      return;
    }
    checkAuth();
  }, [checkAuth]);

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } catch {
      /* ignore */
    }
    setUser(false);
    setTenants([]);
    setSelectedTenantId(null);
  };

  return (
    <AuthContext.Provider
      value={{ user, setUser, tenants, selectedTenantId, setSelectedTenantId, loadTenants, checkAuth, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
