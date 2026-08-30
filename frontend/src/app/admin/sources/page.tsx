import { fetchSources } from "@/lib/api";
import { SourcesPanel } from "@/components/SourcesPanel";
import type { SourceListResponse } from "@/types";

/**
 * Phase 22 — sole-operator source compliance console.
 *
 * Server seeds the table from URL searchParams; client panel owns
 * filter state + Patch Compliance mutation. Mirrors
 * `/admin/subscriptions/page.tsx`.
 */
export default async function AdminSourcesPage({
  searchParams,
}: {
  searchParams?: Record<string, string | string[] | undefined>;
}) {
  const pickFirst = (v: string | string[] | undefined): string | undefined =>
    Array.isArray(v) ? v[0] : v;
  const level = pickFirst(searchParams?.compliance_level);
  const enabled = pickFirst(searchParams?.enabled);

  let initial: SourceListResponse | null = null;
  let errored = false;

  try {
    initial = await fetchSources({
      compliance_level: level && level.length > 0 ? level : undefined,
      limit: 1000,
    });
    // `enabled` filter is post-fetch (no backend param).
    if (initial && enabled && enabled.length > 0) {
      const filtered = initial.items.filter((it) =>
        enabled === "enabled" ? it.enabled : !it.enabled,
      );
      initial = { count: filtered.length, items: filtered };
    }
  } catch {
    errored = true;
  }

  return (
    <main className="container py-10" data-testid="admin-sources-page">
      <header className="mb-8">
        <span className="chip-accent">v2.0 · Admin · Sources</span>
        <h1 className="mt-3 text-3xl font-semibold">Source 合规管理</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          查 / 调 source 的合规级别 (A–E)。每次 PATCH 自动更新 last_compliance_check
          并写一行 AuditLog;A=官方授权,E=明确禁止。所有 mutation 写 AuditLog,
          点行末 「📋」 看行级历史。
        </p>
      </header>

      {errored || !initial ? (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm"
          data-testid="sources-page-error"
        >
          加载失败:后端不可达或 webhook secret 无效。检查 docker compose +
          sessionStorage。
        </p>
      ) : (
        <SourcesPanel
          initial={initial}
          initialFilters={{
            compliance_level: level && level.length > 0 ? level : undefined,
            enabled: enabled && enabled.length > 0 ? enabled : undefined,
          }}
        />
      )}
    </main>
  );
}
