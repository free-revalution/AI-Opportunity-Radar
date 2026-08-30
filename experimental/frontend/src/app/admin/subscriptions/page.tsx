import { fetchSubscriptions } from "@/lib/api";
import { SubscriptionsPanel } from "@/components/SubscriptionsPanel";
import type { SubscriptionListResponse } from "@/types";

/**
 * Phase 22 — sole-operator subscription console.
 *
 * Server seeds the table from URL searchParams; client panel owns
 * filter state + Extend / Cancel mutations.
 */
export default async function AdminSubscriptionsPage({
  searchParams,
}: {
  searchParams?: Record<string, string | string[] | undefined>;
}) {
  const pickFirst = (v: string | string[] | undefined): string | undefined =>
    Array.isArray(v) ? v[0] : v;
  const status = pickFirst(searchParams?.status);
  const plan = pickFirst(searchParams?.plan);
  const feishu = pickFirst(searchParams?.feishu_open_id);

  let initial: SubscriptionListResponse | null = null;
  let errored = false;

  try {
    initial = await fetchSubscriptions({
      status: status && status.length > 0 ? status : undefined,
      plan: plan && plan.length > 0 ? plan : undefined,
      limit: 1000,
    });
    // feishu_open_id text filter is post-fetch (no backend param).
    if (feishu && feishu.length > 0 && initial) {
      const needle = feishu.toLowerCase();
      const filtered = initial.items.filter((it) =>
        (it.feishu_open_id ?? "").toLowerCase().includes(needle),
      );
      initial = { count: filtered.length, items: filtered };
    }
  } catch {
    errored = true;
  }

  return (
    <main className="container py-10" data-testid="admin-subscriptions-page">
      <header className="mb-8">
        <span className="chip-accent">v2.0 · Admin · Subscriptions</span>
        <h1 className="mt-3 text-3xl font-semibold">订阅管理</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          查 / 续 / 取消订阅。Extend 取 (now, expires_at) 较晚者为基准 +N 天
          并把 status 置为 active。所有 mutation 写 AuditLog,
          点行末 「📋」 看行级历史。
        </p>
      </header>

      {errored || !initial ? (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm"
          data-testid="subscriptions-page-error"
        >
          加载失败:后端不可达或 webhook secret 无效。检查 docker compose +
          sessionStorage。
        </p>
      ) : (
        <SubscriptionsPanel
          initial={initial}
          initialFilters={{
            status: status && status.length > 0 ? status : undefined,
            plan: plan && plan.length > 0 ? plan : undefined,
            feishu_open_id:
              feishu && feishu.length > 0 ? feishu : undefined,
          }}
        />
      )}
    </main>
  );
}
