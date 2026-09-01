import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import Layout from "@/components/Layout";
import Login from "@/pages/Login";
import AuthCallback from "@/pages/AuthCallback";
import Overview from "@/pages/Overview";
import Payments from "@/pages/Payments";
import Refunds from "@/pages/Refunds";
import Ledger from "@/pages/Ledger";
import Settlements from "@/pages/Settlements";
import Providers from "@/pages/Providers";
import Fees from "@/pages/Fees";
import Tenants from "@/pages/Tenants";
import AccessControl from "@/pages/AccessControl";
import Features from "@/pages/Features";
import Audit from "@/pages/Audit";
import Monitoring from "@/pages/Monitoring";
import ApiKeys from "@/pages/ApiKeys";
import Checkout from "@/pages/Checkout";
import Webhooks from "@/pages/Webhooks";
import Reports from "@/pages/Reports";
import CheckoutPage from "@/pages/CheckoutPage";
import Security from "@/pages/Security";
import { ForgotPassword, ResetPassword } from "@/pages/PasswordReset";

function FullLoader() {
  return (
    <div className="min-h-screen flex items-center justify-center cp-grid-bg">
      <Loader2 className="h-8 w-8 animate-spin text-primary" />
    </div>
  );
}

function Protected({ children }) {
  const { user } = useAuth();
  if (user === null) return <FullLoader />;
  if (user === false) return <Navigate to="/login" replace />;
  return <Layout>{children}</Layout>;
}

function AppRouter() {
  const location = useLocation();
  if (location.hash?.includes("session_id=")) {
    return <AuthCallback />;
  }
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route path="/checkout/:token" element={<CheckoutPage />} />
      <Route path="/dashboard" element={<Protected><Overview /></Protected>} />
      <Route path="/dashboard/security" element={<Protected><Security /></Protected>} />
      <Route path="/dashboard/payments" element={<Protected><Payments /></Protected>} />
      <Route path="/dashboard/refunds" element={<Protected><Refunds /></Protected>} />
      <Route path="/dashboard/ledger" element={<Protected><Ledger /></Protected>} />
      <Route path="/dashboard/settlements" element={<Protected><Settlements /></Protected>} />
      <Route path="/dashboard/checkout" element={<Protected><Checkout /></Protected>} />
      <Route path="/dashboard/api-keys" element={<Protected><ApiKeys /></Protected>} />
      <Route path="/dashboard/webhooks" element={<Protected><Webhooks /></Protected>} />
      <Route path="/dashboard/reports" element={<Protected><Reports /></Protected>} />
      <Route path="/dashboard/providers" element={<Protected><Providers /></Protected>} />
      <Route path="/dashboard/fees" element={<Protected><Fees /></Protected>} />
      <Route path="/dashboard/tenants" element={<Protected><Tenants /></Protected>} />
      <Route path="/dashboard/access" element={<Protected><AccessControl /></Protected>} />
      <Route path="/dashboard/features" element={<Protected><Features /></Protected>} />
      <Route path="/dashboard/audit" element={<Protected><Audit /></Protected>} />
      <Route path="/dashboard/monitoring" element={<Protected><Monitoring /></Protected>} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <div className="App dark">
      <BrowserRouter>
        <AuthProvider>
          <AppRouter />
        </AuthProvider>
      </BrowserRouter>
    </div>
  );
}
