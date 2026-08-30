/**
 * Phase 18 — Content Center helpers (mirrors backend state machine).
 *
 * `NEXT_STATUS_MAP` is the operator-facing view of the state machine
 * defined in `backend/app/repositories/content_opportunities.py`. The
 * backend rejects illegal transitions with 422; this map prevents the
 * UI from even offering those buttons.
 */

import type { ContentOpportunityStatus } from "@/types";

export const STATUS_LABELS: Record<ContentOpportunityStatus, string> = {
  draft: "草稿",
  approved: "已批准",
  published: "已发布",
  rejected: "已驳回",
  archived: "已归档",
};

/** Legal *outgoing* transitions per status — same set as the backend
 * `_ALLOWED_TRANSITIONS` minus `archived` (no admin endpoint yet). */
export const NEXT_STATUS_MAP: Record<ContentOpportunityStatus, ContentOpportunityStatus[]> = {
  draft: ["approved", "rejected"],
  approved: ["published", "rejected"],
  published: [],
  rejected: [],
  archived: [],
};

export function statusChipClass(status: ContentOpportunityStatus): string {
  switch (status) {
    case "draft":
      return "bg-zinc-500/20 text-zinc-300";
    case "approved":
      return "bg-blue-500/20 text-blue-300";
    case "published":
      return "bg-emerald-500/20 text-emerald-300";
    case "rejected":
      return "bg-red-500/20 text-red-300";
    case "archived":
      return "bg-zinc-700/40 text-zinc-400";
    default:
      return "bg-zinc-500/20 text-zinc-300";
  }
}

/** `0.85` → `"85%"`. Backend stores the float as 0..1. */
export function formatRiskScore(score: number | null | undefined): string {
  if (score === null || score === undefined || Number.isNaN(score)) return "—";
  return `${Math.round(score * 100)}%`;
}

/** ISO → "08/27 10:00" style compact date (matches OrdersPanel). */
export function formatShortDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}/${dd} ${hh}:${mi}`;
}

/** Truncate a hook/title for table display. */
export function truncate(s: string | null | undefined, max: number = 40): string {
  if (!s) return "";
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

/** Render an AuditLog `content_opportunity_transition` metadata dict
 * as a one-liner like "draft → approved". Phase 19 dashboard feed. */
export function formatActivityTransition(
  metadata: Record<string, unknown>,
): string {
  const from = typeof metadata.from === "string" ? metadata.from : "?";
  const to = typeof metadata.to === "string" ? metadata.to : "?";
  return `${from} → ${to}`;
}

/** Optional reason text from a transition (reject carries one). */
export function activityReason(
  metadata: Record<string, unknown>,
): string | null {
  const r = metadata.reason;
  return typeof r === "string" && r.length > 0 ? r : null;
}