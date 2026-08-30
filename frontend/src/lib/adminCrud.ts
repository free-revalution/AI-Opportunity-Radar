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

// ---------------------------------------------------------------------------
// Phase 23 — Notification (IM delivery history) chips + deep links.
// Mirrors `_notification_deep_link` in `backend/app/api/admin.py` —
// keep them in sync so the messages panel's "打开资源" button lands
// somewhere sensible.
// ---------------------------------------------------------------------------
export const NOTIFICATION_KIND_LABELS: Record<string, string> = {
  activation_code_issued: "激活码发放",
  activation_code_resend: "激活码补发",
  subscription_renewal_reminder: "续期提醒",
};

export const NOTIFICATION_KIND_CHIP: Record<string, string> = {
  activation_code_issued: "bg-blue-500/20 text-blue-300",
  activation_code_resend: "bg-violet-500/20 text-violet-300",
  subscription_renewal_reminder: "bg-amber-500/20 text-amber-300",
};

export const NOTIFICATION_CHANNEL_CHIP: Record<string, string> = {
  feishu: "bg-emerald-500/20 text-emerald-300",
  telegram: "bg-sky-500/20 text-sky-300",
};

/** Server-side `payload` → admin route the operator can open in one click. */
export function notificationDeepLink(
  payload: Record<string, unknown>,
): string | null {
  const kind = payload.kind as string | undefined;
  if (
    (kind === "activation_code_issued" || kind === "activation_code_resend") &&
    payload.activation_code_id != null
  ) {
    return `/admin/activation?id=${encodeURIComponent(String(payload.activation_code_id))}`;
  }
  if (kind === "subscription_renewal_reminder" && payload.subscription_id != null) {
    return `/admin/subscriptions?id=${encodeURIComponent(String(payload.subscription_id))}`;
  }
  return null;
}
