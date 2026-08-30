/**
 * Phase 22 — shared helpers for the three admin CRUD pages
 * (activation / subscriptions / sources).
 *
 * Keeps the three panels free of duplicated enum tables and chip-class
 * switches. Plan / status / compliance values mirror the backend
 * Pydantic enums in `backend/app/services/subscriptions.py`,
 * `backend/app/models/activation.py`, and
 * `backend/app/services/compliance/models.py`.
 */

export const ACTIVATION_STATUSES = [
  "unused",
  "active",
  "expired",
  "revoked",
] as const;
export type ActivationStatus = (typeof ACTIVATION_STATUSES)[number];

export const SUBSCRIPTION_STATUSES = [
  "active",
  "expired",
  "suspended",
  "cancelled",
] as const;
export type SubscriptionStatus = (typeof SUBSCRIPTION_STATUSES)[number];

export const COMPLIANCE_LEVELS = ["A", "B", "C", "D", "E"] as const;
export type ComplianceLevel = (typeof COMPLIANCE_LEVELS)[number];

export const PLAN_CODES = ["free", "basic", "pro", "creator"] as const;
export type PlanCode = (typeof PLAN_CODES)[number];

/** Compliance level → human-readable meaning (sole-operator tooltip). */
export const COMPLIANCE_LEVEL_HINTS: Record<ComplianceLevel, string> = {
  A: "官方 API / 明确授权",
  B: "公开页面 / 可限量抓取",
  C: "商业 / 自动门控需人工",
  D: "登录 / 付费墙",
  E: "明确禁止 — 不抓不引用",
};

/** Color class for a chip given an entity kind + status/level value. */
export function statusChipClass(
  kind: "activation" | "subscription" | "compliance",
  value: string,
): string {
  if (kind === "activation") {
    switch (value as ActivationStatus) {
      case "unused":
        return "bg-zinc-500/20 text-zinc-300";
      case "active":
        return "bg-blue-500/20 text-blue-300";
      case "expired":
        return "bg-zinc-700/40 text-zinc-400";
      case "revoked":
        return "bg-red-500/20 text-red-300";
      default:
        return "bg-muted text-muted-foreground";
    }
  }
  if (kind === "subscription") {
    switch (value as SubscriptionStatus) {
      case "active":
        return "bg-emerald-500/20 text-emerald-300";
      case "expired":
        return "bg-zinc-700/40 text-zinc-400";
      case "suspended":
        return "bg-amber-500/20 text-amber-300";
      case "cancelled":
        return "bg-red-500/20 text-red-300";
      default:
        return "bg-muted text-muted-foreground";
    }
  }
  // compliance
  switch (value as ComplianceLevel) {
    case "A":
      return "bg-emerald-500/20 text-emerald-300";
    case "B":
      return "bg-blue-500/20 text-blue-300";
    case "C":
      return "bg-amber-500/20 text-amber-300";
    case "D":
      return "bg-red-500/20 text-red-300";
    case "E":
      return "bg-red-700/40 text-red-400";
    default:
      return "bg-muted text-muted-foreground";
  }
}

/** resource_type → "/admin/audit-logs?resource_type=&resource_id=" deep link. */
export function auditDeepLink(
  resourceType: string,
  resourceId: number | string,
): string {
  const params = new URLSearchParams({
    resource_type: resourceType,
    resource_id: String(resourceId),
  });
  return `/admin/audit-logs?${params.toString()}`;
}
