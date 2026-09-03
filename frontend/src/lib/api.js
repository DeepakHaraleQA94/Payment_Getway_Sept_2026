import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

export const api = axios.create({
  baseURL: API,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

export function formatApiError(detail) {
  if (detail == null) return "Something went wrong. Please try again.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((e) => (e && typeof e.msg === "string" ? e.msg : JSON.stringify(e)))
      .filter(Boolean)
      .join(" ");
  if (detail && typeof detail.msg === "string") return detail.msg;
  return String(detail);
}

// Minor-unit precision per ISO-4217 currency (default 2). Zero-decimal (e.g. JPY) and
// three-decimal (e.g. KWD) currencies are handled so amounts are never off by 100.
export const CURRENCY_DECIMALS = {
  JPY: 0, KRW: 0, VND: 0, CLP: 0, XOF: 0, XAF: 0, PYG: 0,
  BHD: 3, KWD: 3, OMR: 3, TND: 3,
};
export function currencyDecimals(currency = "USD") {
  const d = CURRENCY_DECIMALS[(currency || "").toUpperCase()];
  return d === undefined ? 2 : d;
}

export function toMinorUnits(amount, currency = "USD") {
  const d = currencyDecimals(currency);
  return Math.round(parseFloat(amount) * Math.pow(10, d));
}

export function currencySymbol(currency = "USD") {
  try {
    const parts = new Intl.NumberFormat("en-US", { style: "currency", currency }).formatToParts(0);
    const sym = parts.find((p) => p.type === "currency");
    return sym ? sym.value : currency;
  } catch {
    return currency;
  }
}

export function money(minor, currency = "USD") {
  const d = currencyDecimals(currency);
  const value = (minor || 0) / Math.pow(10, d);
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(value);
  } catch {
    return `${currency} ${value.toFixed(d)}`;
  }
}

export async function downloadCsv(path, params, filename) {
  const res = await api.get(path, { params, responseType: "blob" });
  const url = window.URL.createObjectURL(new Blob([res.data], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}
