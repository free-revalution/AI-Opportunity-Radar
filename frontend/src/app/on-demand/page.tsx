import { fetchOnDemandRecent } from "@/lib/api";

import { OnDemandPanel } from "@/components/OnDemandPanel";

/**
 * On-demand deep-research page — Phase 5 (v2.0).
 *
 * Server component: pulls the initial recent-jobs list, hands off to
 * the client-side OnDemandPanel for form submission + report viewing.
 * Falls back to an empty list if the backend is offline.
 */
export default async function OnDemandPage() {
  let initialList: Awaited<ReturnType<typeof fetchOnDemandRecent>>;
  let errored = false;
  try {
    initialList = await fetchOnDemandRecent(20);
  } catch {
    errored = true;
    initialList = {
      generated_at: new Date().toISOString(),
      items: [],
      total: 0,
    };
  }

  return (
    <main className="container py-10" data-testid="on-demand-page">
      <header className="mb-8">
        <span className="chip-accent">v2.0 · 按需深度研究报告</span>
        <h1 className="mt-3 text-3xl font-semibold">按需深度研究报告</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          客户付费 (¥299-¥999/份) 后,在这里输入一个公开 URL 或一个主题,
          系统会用与日常流水线相同的抓取+LLM 流程即时生成七段式研究报告,
          并把销售作为订单登记。
        </p>
      </header>

      {errored ? (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm"
          data-testid="on-demand-page-error"
        >
          加载失败:后端不可达。检查 docker compose 状态。
        </p>
      ) : (
        <OnDemandPanel initialList={initialList} />
      )}
    </main>
  );
}