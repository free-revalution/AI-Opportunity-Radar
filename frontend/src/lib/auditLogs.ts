/**
 * Phase 20 — /admin/audit-logs viewer helpers.
 *
 * Static enum value lists for the filter chips/selects, plus small
 * pretty-printers that map backend AuditLog metadata_json (which is
 * shape-per-action) into something the table row can display without
 * blowing up its layout.
 */

import { formatActivityTransition } from "@/lib/contentOpportunities";

export const ACTOR_TYPE_OPTIONS = [
  "admin",
  "system",
  "user",
  "bot",
] as const;
export type ActorType = (typeof ACTOR_TYPE_OPTIONS)[number];

export const RESULT_OPTIONS = [
  "success",
  "failure",
  "blocked",
  "partial",
] as const;
export type AuditResultValue = (typeof RESULT_OPTIONS)[number];

/** Full set of action strings we know about. New actions can be added
 * here without changing the backend — they pass through as free-form
 * strings on the AuditLog model. */
export const ACTION_OPTIONS = [
  "publish",
  "reject",
  "research",
  "refresh",
  "score",
  "activate",
  "content_generate",
  "source_enable",
  "source_disable",
  "rbac_deny",
  "compliance_block",
  "content_opportunity_transition",
  "activation_issue",
  "activation_revoke",
  "subscription_extend",
  "subscription_cancel",
  "source_compliance_update",
] as const;

/** One-line summary for the metadata cell — keeps the table row dense. */
export function formatActionMetadata(
  action: string,
  meta: Record<string, unknown> | null | undefined,
): string {
  if (!meta) return "—";
  switch (action) {
    case "content_opportunity_transition":
      return formatActivityTransition(meta);
    case "activation_issue":
      return `plan=${meta.plan ?? "?"} ttl=${meta.ttl_days ?? "?"}d`;
    case "activation_revoke":
      return "revoked";
    case "subscription_extend":
      return `+${meta.days ?? "?"}d → ${meta.new_expires_at ?? "?"}`;
    case "subscription_cancel":
      return "cancelled";
    case "source_compliance_update":
      return `level=${meta.new_compliance_level ?? "?"}`;
    default:
      return Object.entries(meta)
        .map(([k, v]) => `${k}=${String(v)}`)
        .join(" ");
  }
}

/** resource_type + id → clickable URL + label, when applicable. */
export function resourceLink(
  resourceType: string | null,
  resourceId: string | null,
): { href: string | null; label: string } {
  if (!resourceType || !resourceId) return { href: null, label: "—" };
  if (resourceType === "content_opportunity") {
    return {
      href: `/admin/content-opportunities/${resourceId}`,
      label: `#${resourceId}`,
    };
  }
  // activation_code / subscription / source / etc. — no dedicated viewer page.
  return { href: null, label: `${resourceType}#${resourceId}` };
}

export function actorTypeClass(actorType: string): string {
  switch (actorType) {
    case "admin":
      return "bg-blue-500/20 text-blue-300";
    case "system":
      return "bg-zinc-500/20 text-zinc-300";
    case "user":
      return "bg-emerald-500/20 text-emerald-300";
    case "bot":
      return "bg-violet-500/20 text-violet-300";
    default:
      return "bg-muted text-muted-foreground";
  }
}

export function resultClass(result: string): string {
  switch (result) {
    case "success":
      return "bg-emerald-500/20 text-emerald-300";
    case "failure":
      return "bg-red-500/20 text-red-300";
    case "blocked":
      return "bg-amber-500/20 text-amber-300";
    case "partial":
      return "bg-zinc-500/20 text-zinc-300";
    default:
      return "bg-muted text-muted-foreground";
  }
}
