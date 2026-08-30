import Link from "next/link";

import { fetchHealth } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Top navigation bar — visible on every page.
 *
 * Renders a tiny health pill that reflects `/api/health` so the operator
 * can spot backend issues without leaving the dashboard.
 */
export async function SiteHeader() {
  let health: Awaited<ReturnType<typeof fetchHealth>> | null = null;
  let healthError = false;
  try {
    health = await fetchHealth();
  } catch {
    healthError = true;
  }

  const healthLabel = healthError
    ? "offline"
    : health?.status === "healthy"
      ? "healthy"
      : health?.status === "degraded"
        ? "degraded"
        : "down";

  const healthTone =
    healthLabel === "healthy"
      ? "chip-success"
      : healthLabel === "degraded"
        ? "chip-warning"
        : "chip-danger";

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-background/80 backdrop-blur">
      <div className="container flex h-14 items-center justify-between gap-6">
        <Link
          href="/"
          className="flex items-center gap-2 text-sm font-semibold tracking-tight"
        >
          <span aria-hidden className="inline-block h-2 w-2 rounded-full bg-accent" />
          AI Opportunity Radar
        </Link>

        <nav className="flex items-center gap-4 text-sm">
          <Link href="/dashboard" className="hover:text-accent">
            Dashboard
          </Link>
          <Link href="/opportunities" className="hover:text-accent">
            Opportunities
          </Link>
          <Link href="/content-center" className="hover:text-accent">
            Content Center
          </Link>
          <Link href="/orders" className="hover:text-accent">
            Orders
          </Link>
          <Link href="/on-demand" className="hover:text-accent">
            On-demand
          </Link>
          <span
            aria-hidden
            className="mx-1 h-4 w-px bg-border"
            title="admin"
          />
          <Link
            href="/admin/content-opportunities"
            className="hover:text-accent"
            data-testid="nav-admin-content-opportunities"
          >
            Content Center (Admin)
          </Link>
          <Link
            href="/admin/signals"
            className="hover:text-accent"
            data-testid="nav-admin-signals"
          >
            Signals (Admin)
          </Link>
          <Link
            href="/admin/audit-logs"
            className="hover:text-accent"
            data-testid="nav-admin-audit-logs"
          >
            Audit Log
          </Link>
          <Link href="/settings" className="hover:text-accent">
            Settings
          </Link>
          <span
            className={cn("chip", healthTone)}
            title={
              healthError
                ? "Backend not reachable"
                : `Backend status: ${healthLabel}`
            }
            data-testid="health-pill"
          >
            <span
              aria-hidden
              className={cn(
                "inline-block h-1.5 w-1.5 rounded-full",
                healthLabel === "healthy"
                  ? "bg-success"
                  : healthLabel === "degraded"
                    ? "bg-warning"
                    : "bg-danger",
              )}
            />
            {healthLabel}
          </span>
        </nav>
      </div>
    </header>
  );
}
