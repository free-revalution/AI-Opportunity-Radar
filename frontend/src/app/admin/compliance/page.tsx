import { fetchComplianceAudits } from "@/lib/api";
import { CompliancePanel } from "@/components/CompliancePanel";
import type { ComplianceAuditFilters } from "@/types";

/**
 * Phase 24 — Compliance operator landing page.
 *
 * Mirrors `audit-logs/page.tsx`: server component reads URL searchParams,
 * calls `fetchComplianceAudits`, hands the snapshot to `<CompliancePanel/>`
 * which owns client-side filter + override state.
 */
export default async function AdminCompliancePage({
  searchParams,
}: {
  searchParams?: Record<string, string | string[] | undefined>;
}) {
  const pickFirst = (v: string | string[] | undefined): string | undefined =>
    Array.isArray(v) ? v[0] : v;
  const str = (k: keyof ComplianceAuditFilters): string | undefined => {
    const v = pickFirst(searchParams?.[k]);
    return v && v.length > 0 ? v : undefined;
  };
  const filters: ComplianceAuditFilters = {
    risk_level:
      (str("risk_level") as ComplianceAuditFilters["risk_level"]) ?? "",
    risk_type: (str("risk_type") as ComplianceAuditFilters["risk_type"]) ?? "",
    resource_type: str("resource_type"),
    since: str("since"),
    limit: 50,
    offset: 0,
  };

  let initial: Awaited<ReturnType<typeof fetchComplianceAudits>> | null = null;
  let errored = false;

  try {
    initial = await fetchComplianceAudits(filters);
  } catch {
    errored = true;
  }

  return (
    <main className="container py-10" data-testid="compliance-page">
      <header className="mb-8">
        <span className="chip-accent">v2.0 · Admin · Compliance</span>
        <h1 className="mt-3 text-3xl font-semibold">合规审计</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          全量合规阻断记录:每个外发 / 内容生成 / 源抓取在 Phase 24 都过
          gate,任何 HIGH / BLOCKED 判定都会写一条 audit log。这里是
          operator 审 + override 的入口。
          <br />
          共 <span className="font-mono">{initial?.total ?? 0}</span> 条匹配。
        </p>
      </header>

      {errored || !initial ? (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm"
          data-testid="compliance-error-banner"
        >
          加载失败:后端不可达或 webhook secret 无效。检查 docker compose +
          sessionStorage。
        </p>
      ) : (
        <CompliancePanel initial={initial} initialFilters={filters} />
      )}
    </main>
  );
}
