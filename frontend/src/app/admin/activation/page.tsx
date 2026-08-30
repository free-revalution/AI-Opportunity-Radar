import { fetchActivationCodes } from "@/lib/api";
import { ActivationCodesPanel } from "@/components/ActivationCodesPanel";
import type { ActivationListResponse } from "@/types";

/**
 * Phase 22 — sole-operator activation code console.
 *
 * Server seeds the table from URL searchParams; the client panel owns
 * filter state and re-fetches on filter change. Mirrors
 * `/admin/audit-logs/page.tsx` so the operator's reload/back/forward
 * behavior is consistent across admin pages.
 */
export default async function AdminActivationPage({
  searchParams,
}: {
  searchParams?: Record<string, string | string[] | undefined>;
}) {
  const pickFirst = (v: string | string[] | undefined): string | undefined =>
    Array.isArray(v) ? v[0] : v;
  const status = pickFirst(searchParams?.status);
  const plan = pickFirst(searchParams?.plan);
  const id = pickFirst(searchParams?.id);

  let initial: ActivationListResponse | null = null;
  let errored = false;

  try {
    initial = await fetchActivationCodes({
      status: status && status.length > 0 ? status : undefined,
      plan: plan && plan.length > 0 ? plan : undefined,
      limit: 1000,
    });
    // Server-side id filter (text input is post-filter on the client).
    if (id && id.length > 0 && initial) {
      const n = Number.parseInt(id, 10);
      if (Number.isFinite(n)) {
        initial = {
          count: initial.items.filter((it) => it.id === n).length,
          items: initial.items.filter((it) => it.id === n),
        };
      }
    }
  } catch {
    errored = true;
  }

  return (
    <main className="container py-10" data-testid="admin-activation-page">
      <header className="mb-8">
        <span className="chip-accent">v2.0 · Admin · Activation</span>
        <h1 className="mt-3 text-3xl font-semibold">激活码管理</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          发激活码 → 用户用其绑定飞书 open_id 到某 plan 订阅。明文只展示一次,
          撤销后无法继续用作激活。所有 mutation 写 AuditLog,
          点行末 「📋」 看行级历史。
        </p>
      </header>

      {errored || !initial ? (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm"
          data-testid="activation-page-error"
        >
          加载失败:后端不可达或 webhook secret 无效。检查 docker compose +
          sessionStorage。
        </p>
      ) : (
        <ActivationCodesPanel
          initial={initial}
          initialFilters={{
            status: status && status.length > 0 ? status : undefined,
            plan: plan && plan.length > 0 ? plan : undefined,
            id: id && id.length > 0 ? id : undefined,
          }}
        />
      )}
    </main>
  );
}
