import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null); // null=checking, false=anon, object=authed
  const [tenants, setTenants] = useState([]);
  const [selectedTenantId, setSelectedTenantId] = useState(null);
  const [features, setFeatures] = useState({}); // { key: enabled } for selected tenant

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
    if (window.location.hash?.includes("session_id=")) return;
    checkAuth();
  }, [checkAuth]);

  // Load the selected tenant's feature entitlements so the UI can hide disabled features.
  useEffect(() => {
    if (!user || !selectedTenantId) { setFeatures({}); return; }
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get("/features", { params: { tenant_id: selectedTenantId } });
        if (!cancelled) setFeatures(Object.fromEntries(data.map((f) => [f.key, f.enabled])));
      } catch {
        if (!cancelled) setFeatures({});
      }
    })();
    return () => { cancelled = true; };
  }, [user, selectedTenantId]);

  const logout = async () => {
    try { await api.post("/auth/logout"); } catch { /* ignore */ }
    setUser(false);
    setTenants([]);
    setSelectedTenantId(null);
    setFeatures({});
  };

  const permissions = new Set(user?.permissions || []);
  const hasPermission = (code) => !!user?.is_superadmin || permissions.has("*") || permissions.has(code);
  // Absence of a flag defaults to enabled (matches backend require_feature semantics).
  const featureEnabled = (key) => features[key] !== false;

  return (
    <AuthContext.Provider
      value={{ user, setUser, tenants, selectedTenantId, setSelectedTenantId, loadTenants,
               checkAuth, logout, hasPermission, featureEnabled }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
