// Server-side reads of the backend for the library admin pages. They run after
// the admin layout has checked the colleague's role, so nothing here is
// reachable without signing in.

const BACKEND_BASE = (
  process.env.DEV_BACKEND_ORIGIN
  || process.env.SC_API_SERVICE_URL
  || process.env.FULL_ARTICLE_SERVICE_URL
  || "http://127.0.0.1:8222"
).replace(/\/$/, "");

export async function backendJson<T>(path: string): Promise<T | null> {
  const response = await fetch(`${BACKEND_BASE}${path}`, { cache: "no-store" });
  if (response.status === 404) return null;
  if (!response.ok) {
    let detail = `后台服务返回 ${response.status}`;
    try {
      const payload = await response.json() as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch { /* keep the status line */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}
