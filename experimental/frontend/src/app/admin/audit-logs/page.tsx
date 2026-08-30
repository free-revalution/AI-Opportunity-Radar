import { fetchAuditLogs } from "@/lib/api";
import { AuditLogsPanel } from "@/components/AuditLogsPanel";
import type { AuditLogFilters } from "@/types";

/**
 * Phase 20 — sole-operator audit viewer landing page.
 *
 * Server component reads the URL searchParams (filter form +
 * pagination), calls `fetchAuditLogs`, and hands the snapshot to
 * `<AuditLogsPanel/>` for client-side filter/expand/pagination.
 *
 * The webhook secret is injected server-side via the same path the
 * dashboard and signals pages use (Phase 19 proven pattern), so no
 * special wiring is needed here.
 */
export default async function AdminAuditLogsPage({
  searchParams,
}: {
  searchParams?: Record<string, string | string[] | undefined>;
}) {
  // Read URL → AuditLogFilters. Empty string / undefined are dropped.
  const pickFirst = (v: string | string[] | undefined): string | undefined =>
    Array.isArray(v) ? v[0] : v;
  const filters: AuditLogFilters = {};
  const str = (k: keyof AuditLogFilters): string | undefined => {
    const v = pickFirst(searchParams?.[k]);
    return v && v.length > 0 ? v : undefined;
  };
  filters.actor_type = str("actor_type");
  filters.actor_id = str("actor_id");
  filters.action = str("action");
  filters.result = str("result");
  filters.resource_type = str("resource_type");
  filters.resource_id = str("resource_id");
  filters.since = str("since");
  filters.until = str("until");
  filters.limit = 50;
  filters.offset = 0;

  let initial: Awaited<ReturnType<typeof fetchAuditLogs>> | null = null;
  let errored = false;

  try {
    initial = await fetchAuditLogs(filters);
  } catch {
    errored = true;
  }

  return (
    <main className="container py-10" data-testid="audit-logs-page">
      <header className="mb-8">
        <span className="chip-accent">v2.0 · Admin · Audit Log</span>
        <h1 className="mt-3 text-3xl font-semibold">审计日志</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          全量审计追踪:操作人 + 动作 + 资源 + 结果 + 时间。按 actor / action /
          result / 资源 / 时间区间过滤,点行展开 metadata_json。
          <br />
          共 <span className="font-mono">{initial?.total ?? 0}</span> 条匹配。
        </p>
      </header>

      {errored || !initial ? (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm"
          data-testid="audit-logs-error-banner"
        >
          加载失败:后端不可达或 webhook secret 无效。检查 docker compose +
          sessionStorage。
        </p>
      ) : (
        <AuditLogsPanel initial={initial} initialFilters={filters} />
      )}
    </main>
  );
}
