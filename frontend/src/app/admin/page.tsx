import { fetchDashboard } from "@/lib/api";
import { DashboardPanel } from "@/components/DashboardPanel";
import type { DashboardResponse } from "@/types";

/**
 * Phase 19 — admin landing page. Replaces the Phase 18 redirect with a
 * real dashboard: status-machine stats, blocked review queue, signal
 * health, and the latest 20 content_opportunity_transition rows.
 *
 * Server component fetches once; the client panel holds the snapshot
 * for the rest of the session. Refresh by browser reload (Phase 19
 * simplification) — Phase 20 will add a ↻ button.
 */
export default async function AdminIndex() {
  let initial: DashboardResponse | null = null;
  let errored = false;
  try {
    initial = await fetchDashboard();
  } catch {
    errored = true;
  }

  return (
    <main
      className="container py-10"
      data-testid="admin-dashboard-page"
    >
      <header className="mb-8">
        <span className="chip-accent">v2.0 · Admin · Dashboard</span>
        <h1 className="mt-3 text-3xl font-semibold">运营总览</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          ContentOpportunity 状态机分布 + 合规复核队列 + Signal 健康度 +
          最近 20 条状态转换流水。点 stat card 跳到预过滤列表,刷新看最新活动。
        </p>
      </header>

      {errored || !initial ? (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm"
          data-testid="admin-dashboard-error"
        >
          加载失败:后端不可达或 webhook secret 无效。检查 docker compose +
          sessionStorage。
        </p>
      ) : (
        <DashboardPanel initial={initial} />
      )}
    </main>
  );
}