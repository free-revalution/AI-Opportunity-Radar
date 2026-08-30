/**
 * Phase 18 — sessionStorage-backed admin auth.
 *
 * Sole-operator console: the admin (you) types the shared secret once
 * per browser session, we keep it in `sessionStorage` and re-attach
 * `X-Radar-Webhook` on every admin API request via `lib/api.ts`.
 *
 * Why sessionStorage and not a build-time env var?
 *   `NEXT_PUBLIC_RADAR_WEBHOOK_SECRET` would ship the secret to every
 *   visitor's browser bundle. sessionStorage keeps it on the operator's
 *   machine only, and lets them rotate the secret without a rebuild.
 *
 * SSR-safe: all helpers short-circuit when `window` is undefined so
 * server components can import them without crashing.
 */

const STORAGE_KEY = "radar.webhookSecret";

function getSessionStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage;
  } catch {
    // sessionStorage can throw in privacy-mode browsers; treat as
    // unavailable rather than crashing the app.
    return null;
  }
}

export function getWebhookSecret(): string | undefined {
  const s = getSessionStorage();
  if (!s) return undefined;
  const v = s.getItem(STORAGE_KEY);
  return v && v.length > 0 ? v : undefined;
}

export function setWebhookSecret(secret: string): void {
  const s = getSessionStorage();
  if (!s) return;
  s.setItem(STORAGE_KEY, secret);
}

export function clearWebhookSecret(): void {
  const s = getSessionStorage();
  if (!s) return;
  s.removeItem(STORAGE_KEY);
}