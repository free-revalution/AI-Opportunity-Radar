import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatScore(value: number | undefined | null): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "—";
  return `${Math.round(value)}/100`;
}

export function recommendationLabel(
  r: string | undefined,
): { label: string; cls: string } {
  switch (r) {
    case "strongly_recommend":
      return { label: "🔥 Strongly Recommended", cls: "chip-success" };
    case "recommend":
      return { label: "Recommended", cls: "chip-accent" };
    case "watch":
      return { label: "Watch", cls: "chip-warning" };
    case "not_recommended":
      return { label: "Not Recommended", cls: "chip-danger" };
    default:
      return { label: "Insufficient Data", cls: "chip" };
  }
}