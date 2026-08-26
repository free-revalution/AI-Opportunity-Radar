import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

import type { Recommendation } from "@/types";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatScore(value: number | undefined | null): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "—";
  return `${Math.round(value)}/100`;
}

/** Clamp a sub-score into the [0, 100] bar width range. */
export function scoreBarWidth(value: number | undefined | null): number {
  if (value === undefined || value === null || Number.isNaN(value)) return 0;
  if (value < 0) return 0;
  if (value > 100) return 100;
  return value;
}

export function recommendationLabel(
  r: string | undefined,
): { label: string; cls: string; emoji: string } {
  switch (r) {
    case "strongly_recommend":
      return {
        label: "Strongly Recommended",
        cls: "chip-success",
        emoji: "🔥",
      };
    case "recommend":
      return { label: "Recommended", cls: "chip-accent", emoji: "✅" };
    case "watch":
      return { label: "Watch", cls: "chip-warning", emoji: "👀" };
    case "not_recommended":
      return { label: "Not Recommended", cls: "chip-danger", emoji: "⛔" };
    default:
      return {
        label: "Insufficient Data",
        cls: "chip",
        emoji: "⚪",
      };
  }
}

/** Sub-score bands for the breakdown component. */
export const SUB_SCORE_LABELS: ReadonlyArray<{
  key:
    | "trend_score"
    | "demand_score"
    | "monetization_score"
    | "competition_gap_score"
    | "china_gap_score"
    | "execution_score";
  label: string;
  hint: string;
}> = [
  { key: "trend_score", label: "Trend Velocity", hint: "0.20" },
  { key: "demand_score", label: "Demand", hint: "0.20" },
  {
    key: "monetization_score",
    label: "Monetization",
    hint: "0.20",
  },
  {
    key: "competition_gap_score",
    label: "Competition Gap",
    hint: "0.15",
  },
  { key: "china_gap_score", label: "China Gap", hint: "0.15" },
  {
    key: "execution_score",
    label: "Execution Feasibility",
    hint: "0.10",
  },
] as const;

/** "5m ago" / "2h ago" / "3d ago" — fallback to the full date. */
export function formatRelativeTime(iso: string | undefined | null): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const delta = Date.now() - t;
  const abs = Math.abs(delta);
  const future = delta < 0;
  const min = 60_000;
  const hour = 60 * min;
  const day = 24 * hour;
  let label: string;
  if (abs < min) label = "just now";
  else if (abs < hour) label = `${Math.round(abs / min)}m ago`;
  else if (abs < day) label = `${Math.round(abs / hour)}h ago`;
  else label = `${Math.round(abs / day)}d ago`;
  return future ? `in ${label.replace(" ago", "")}` : label;
}

export function recommendationFromScore(score: number | undefined | null): {
  value: Recommendation;
  cls: string;
  emoji: string;
} {
  const s = score ?? 0;
  if (s >= 85) return { value: "strongly_recommend", cls: "chip-success", emoji: "🔥" };
  if (s >= 70) return { value: "recommend", cls: "chip-accent", emoji: "✅" };
  if (s >= 55) return { value: "watch", cls: "chip-warning", emoji: "👀" };
  if (s > 0) return { value: "not_recommended", cls: "chip-danger", emoji: "⛔" };
  return { value: "insufficient_data", cls: "chip", emoji: "⚪" };
}
