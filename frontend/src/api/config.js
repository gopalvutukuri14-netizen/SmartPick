const DEFAULT_DEV_API_BASE = "http://localhost:8000";

/**
 * Resolve and normalize the backend API base URL.
 *
 * Uses VITE_API_BASE_URL when configured (stripping trailing slashes),
 * and falls back to http://localhost:8000 for local development.
 * An explicit empty string or "/" can be used for same-origin deployments.
 */
export function resolveApiBaseUrl(envUrl = import.meta.env?.VITE_API_BASE_URL) {
  if (typeof envUrl === "string") {
    const trimmed = envUrl.trim();
    if (trimmed === "/") {
      return "";
    }
    if (trimmed.length > 0) {
      return trimmed.replace(/\/+$/, "");
    }
  }
  return DEFAULT_DEV_API_BASE;
}

export const API_BASE = resolveApiBaseUrl();

export function buildApiUrl(path) {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE}${normalizedPath}`;
}
